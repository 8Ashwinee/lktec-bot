from __future__ import annotations

import pytest

from lktec_bot.application.gate import (
    POLICY_READ_PERMISSIONS,
    Gate,
    VerbOutcome,
    VerbRequest,
    VerbResult,
)
from lktec_bot.application.verbs import HANDLERS, register
from lktec_bot.domain.permissions import Target, Verb, as_token_request, permissions_for
from lktec_bot.domain.policy import POLICY_PATH
from lktec_bot.ports.github_api import GitHubApi
from tests.fakes import FakeApiFactory, FakeTokenProvider

ENABLED = "version: 1\nmodules:\n  automerge:\n    enabled: true\n"
DISABLED = "version: 1\nmodules:\n  automerge:\n    enabled: false\n"


def request(verb: Verb = Verb.PING, module: str = "automerge") -> VerbRequest:
    return VerbRequest(owner="acme", repo="widgets", module=module, verb=verb)


def make_gate(
    policy_text: str | None,
    *,
    handlers: dict[Verb, object] | None = None,
    caller_token: str | None = None,
) -> tuple[Gate, FakeTokenProvider, FakeApiFactory]:
    files = {POLICY_PATH: policy_text} if policy_text is not None else {}
    tokens = FakeTokenProvider()
    factory = FakeApiFactory(files)
    registry = handlers if handlers is not None else HANDLERS
    gate = Gate(tokens, factory, registry, caller_token=caller_token)  # type: ignore[arg-type]
    return gate, tokens, factory


def test_allowed_verb_runs_with_a_token_narrowed_to_its_permissions() -> None:
    gate, tokens, factory = make_gate(ENABLED)

    result = gate.run(request())

    assert result.outcome is VerbOutcome.OK
    assert result.ok
    assert result.message == "pong from acme/widgets"
    # Exactly two tokens: one to read the policy, one for the verb.
    assert [permissions for _, _, permissions in tokens.requests] == [
        dict(POLICY_READ_PERMISSIONS),
        as_token_request(permissions_for(Verb.PING)),
    ]
    assert all(token.revoked for token in tokens.minted)
    assert all(token.revoke_calls == 1 for token in tokens.minted)
    # The verb's client is bound to the verb token, the policy read to the policy token.
    assert [api.token for api in factory.created] == ["tok-0", "tok-1"]
    assert factory.created[0].calls == [
        ("get_file_content", ("acme", "widgets", POLICY_PATH, None))
    ]
    assert factory.created[1].calls == []


def test_disabled_module_is_denied_and_no_verb_token_is_minted() -> None:
    gate, tokens, _ = make_gate(DISABLED)

    result = gate.run(request())

    assert result.outcome is VerbOutcome.DENIED
    assert "disabled" in result.message
    assert [permissions for _, _, permissions in tokens.requests] == [dict(POLICY_READ_PERMISSIONS)]
    assert tokens.minted[0].revoked


def test_missing_policy_file_is_denied() -> None:
    gate, tokens, _ = make_gate(None)

    result = gate.run(request())

    assert result.outcome is VerbOutcome.DENIED
    assert "missing" in result.message
    assert len(tokens.minted) == 1
    assert tokens.minted[0].revoked


def test_undeclared_module_is_denied() -> None:
    gate, _, _ = make_gate(ENABLED)

    result = gate.run(request(module="release"))

    assert result.outcome is VerbOutcome.DENIED
    assert "not declared" in result.message


def test_invalid_policy_file_is_denied_with_reasons() -> None:
    gate, tokens, _ = make_gate("version: 1\nmodules:\n  automerge:\n    enabled: true\n    x: 1\n")

    result = gate.run(request())

    assert result.outcome is VerbOutcome.DENIED
    assert "invalid" in result.message
    assert "x: Extra inputs are not permitted" in result.message
    assert len(tokens.minted) == 1


def test_handler_exception_becomes_error_and_token_is_still_revoked() -> None:
    def boom(api: GitHubApi, req: VerbRequest) -> VerbResult:
        raise RuntimeError("kaboom")

    gate, tokens, _ = make_gate(ENABLED, handlers={Verb.PING: boom})

    result = gate.run(request())

    assert result.outcome is VerbOutcome.ERROR
    assert "kaboom" in result.message
    assert len(tokens.minted) == 2
    assert all(token.revoked for token in tokens.minted)


def test_policy_read_failure_is_an_error_not_a_crash() -> None:
    class Broken:
        def __init__(self, token: str) -> None:
            self.token = token

        def get_file_content(self, *args: object, **kwargs: object) -> str | None:
            raise ConnectionError("api down")

    tokens = FakeTokenProvider()
    gate = Gate(tokens, Broken, HANDLERS)  # type: ignore[arg-type]

    result = gate.run(request())

    assert result.outcome is VerbOutcome.ERROR
    assert "api down" in result.message
    assert tokens.minted[0].revoked


def test_token_minting_failure_is_an_error() -> None:
    gate, tokens, _ = make_gate(ENABLED, caller_token="caller")
    tokens.fail_with = RuntimeError("app not installed")

    result = gate.run(request())

    assert result.outcome is VerbOutcome.ERROR
    assert "app not installed" in result.message


def test_caller_token_reads_the_policy_without_minting() -> None:
    gate, tokens, factory = make_gate(ENABLED, caller_token="ghs_caller")

    result = gate.run(request())

    assert result.ok
    assert [permissions for _, _, permissions in tokens.requests] == [
        as_token_request(permissions_for(Verb.PING))
    ]
    assert factory.created[0].token == "ghs_caller"
    assert factory.created[1].token == "tok-0"


def test_unregistered_verb_is_an_error_before_any_token() -> None:
    gate, tokens, _ = make_gate(ENABLED, handlers={})

    result = gate.run(request())

    assert result.outcome is VerbOutcome.ERROR
    assert "no handler" in result.message
    assert tokens.requests == []


def test_comment_without_target_is_an_error_after_policy() -> None:
    def comment(api: GitHubApi, req: VerbRequest) -> VerbResult:
        return VerbResult(VerbOutcome.OK, "commented")

    gate, tokens, _ = make_gate(ENABLED, handlers={Verb.COMMENT: comment})

    result = gate.run(request(verb=Verb.COMMENT))

    assert result.outcome is VerbOutcome.ERROR
    assert "target" in result.message
    assert len(tokens.minted) == 1  # policy read only


def test_comment_target_selects_the_permission() -> None:
    def comment(api: GitHubApi, req: VerbRequest) -> VerbResult:
        return VerbResult(VerbOutcome.OK, "commented", url="https://example.invalid/c/1")

    gate, tokens, _ = make_gate(ENABLED, handlers={Verb.COMMENT: comment})
    req = VerbRequest("acme", "widgets", "automerge", Verb.COMMENT, target=Target.ISSUE)

    result = gate.run(req)

    assert result.ok
    assert tokens.requests[-1][2] == {"issues": "write"}
    assert result.url == "https://example.invalid/c/1"


def test_result_markdown_mentions_verb_repo_and_url() -> None:
    req = request()
    text = VerbResult(VerbOutcome.OK, "pong", url="https://example.invalid").to_markdown(req)

    assert "`ping`" in text
    assert "`acme/widgets`" in text
    assert "https://example.invalid" in text
    assert text.endswith("\n")
    assert "DENIED" in VerbResult(VerbOutcome.DENIED, "no").to_markdown(req)


def test_register_refuses_duplicates() -> None:
    def handler(api: GitHubApi, req: VerbRequest) -> VerbResult:
        return VerbResult(VerbOutcome.OK, "")

    with pytest.raises(ValueError, match="already registered"):
        register(Verb.PING)(handler)
