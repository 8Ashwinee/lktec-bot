from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
import typer.main
from typer.testing import CliRunner

from lktec_bot import __version__
from lktec_bot.adapters.app_token import AppTokenProvider
from lktec_bot.adapters.github_http import HttpGitHubApi
from lktec_bot.application.gate import Gate, VerbOutcome, VerbRequest, VerbResult
from lktec_bot.application.verbs import handlers
from lktec_bot.cli import ConfigError, app, build_gate, token_provider_from_env
from lktec_bot.domain.permissions import Verb
from lktec_bot.ports.github_api import GitHubApi
from lktec_bot.ports.token_provider import TokenError
from lktec_bot.summary import mask_secret
from tests.fakes import FakeApiFactory, FakeTokenProvider
from tests.test_key_source import PEM

runner = CliRunner()

VALID = "version: 1\nmodules:\n  ping:\n    enabled: true\n"
AUTOMERGE = "version: 1\nmodules:\n  automerge:\n    enabled: true\n"
APP_ENV = {"LKTEC_BOT_APP_ID": "1", "LKTEC_BOT_PRIVATE_KEY": PEM}


@pytest.fixture
def fake_gate(monkeypatch: pytest.MonkeyPatch) -> FakeTokenProvider:
    """Replace ``build_gate`` with one wired to the fakes; returns the provider for asserts."""
    tokens = FakeTokenProvider()
    factory = FakeApiFactory({".github/lktec-bot.yml": VALID})

    def fake_build_gate(env: Mapping[str, str] | None = None) -> Gate:
        return Gate(tokens, factory, handlers(), mask=mask_secret)

    monkeypatch.setattr("lktec_bot.cli.build_gate", fake_build_gate)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return tokens


def write_policy(tmp_path: Path, text: str) -> Path:
    policy = tmp_path / "lktec-bot.yml"
    policy.write_text(text, encoding="utf-8")
    return policy


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_pretty_exceptions_are_off() -> None:
    assert app.pretty_exceptions_enable is False
    assert app.pretty_exceptions_show_locals is False


# -- policy subcommands -------------------------------------------------------------------


def test_policy_validate_ok(tmp_path: Path) -> None:
    result = runner.invoke(app, ["policy", "validate", str(write_policy(tmp_path, VALID))])

    assert result.exit_code == 0
    assert "valid: version 1, enabled modules: ping" in result.output


def test_policy_validate_reports_every_reason(tmp_path: Path) -> None:
    policy = write_policy(tmp_path, "version: 1\nmodules:\n  automerge:\n    nope: 1\n  x: {}\n")

    result = runner.invoke(app, ["policy", "validate", str(policy)])

    assert result.exit_code == 1
    assert "invalid: modules.automerge.nope: Extra inputs are not permitted" in result.output
    assert "invalid: modules.x: unknown module (known: automerge, ping)" in result.output


def test_policy_validate_reports_structural_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["policy", "validate", str(write_policy(tmp_path, "version: 3\n"))])

    assert result.exit_code == 1
    assert "invalid: version: Input should be 1" in result.output


def test_policy_check_allowed(tmp_path: Path) -> None:
    policy = write_policy(tmp_path, AUTOMERGE)

    result = runner.invoke(
        app,
        ["policy", "check", "--file", str(policy), "--module", "automerge", "--verb", "approve"],
    )

    assert result.exit_code == 0
    assert "approve for automerge: allowed" in result.output


def test_policy_check_denies_a_verb_outside_the_module_set(tmp_path: Path) -> None:
    policy = write_policy(tmp_path, AUTOMERGE)

    result = runner.invoke(
        app, ["policy", "check", "-f", str(policy), "-m", "automerge", "-v", "ping"]
    )

    assert result.exit_code == 3
    assert "ping for automerge: denied" in result.output
    assert "may not run 'ping'" in result.output


def test_policy_check_denies_an_unknown_module(tmp_path: Path) -> None:
    policy = write_policy(tmp_path, VALID)

    result = runner.invoke(
        app, ["policy", "check", "-f", str(policy), "-m", "release", "-v", "release"]
    )

    assert result.exit_code == 3
    assert "release for release: denied" in result.output
    assert "not known" in result.output


def test_policy_check_without_file_simulates_a_missing_policy() -> None:
    result = runner.invoke(app, ["policy", "check", "-m", "ping", "-v", "ping"])

    assert result.exit_code == 3
    assert "missing" in result.output


def test_policy_check_invalid_file(tmp_path: Path) -> None:
    policy = write_policy(tmp_path, "version: 1\nextra: 1\n")

    result = runner.invoke(app, ["policy", "check", "-f", str(policy), "-m", "a", "-v", "ping"])

    assert result.exit_code == 3
    assert "denied" in result.output
    assert "extra: Extra inputs are not permitted" in result.output


def test_policy_check_rejects_unknown_verb() -> None:
    result = runner.invoke(app, ["policy", "check", "-m", "ping", "-v", "merge"])

    assert result.exit_code == 2


# -- verb subcommands ---------------------------------------------------------------------


def test_ping_command_runs_through_the_gate(
    fake_gate: FakeTokenProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert result.exit_code == 0, result.output
    assert "ok: pong from acme/widgets" in result.output
    assert "`ping`" in summary.read_text(encoding="utf-8")
    assert fake_gate.requests[-1][2] == {"metadata": "read"}
    assert all(token.revoked for token in fake_gate.minted)
    assert fake_gate.closed


def test_ping_command_denied_exits_3(fake_gate: FakeTokenProvider) -> None:
    result = runner.invoke(app, ["ping", "--repository", "acme/widgets", "--module", "automerge"])

    assert result.exit_code == 3
    assert "denied:" in result.output
    assert "not declared" in result.output


def test_ping_command_error_exits_1(fake_gate: FakeTokenProvider) -> None:
    fake_gate.fail_with = TokenError("the App is not installed on acme/widgets")

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert result.exit_code == 1
    assert "error: could not mint the policy token: the App is not installed" in result.output


def test_ping_command_prints_warnings_without_changing_the_outcome(
    fake_gate: FakeTokenProvider,
) -> None:
    fake_gate.failing_revokes = 5

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert result.exit_code == 0
    assert "ok: pong from acme/widgets" in result.stdout
    assert result.stderr.count("warning: token not revoked") == 2


@pytest.mark.parametrize("repository", ["no-slash", "a/b/c", "a/../b", "/b", "a/", "a b/c", ""])
def test_ping_command_rejects_bad_repository(fake_gate: FakeTokenProvider, repository: str) -> None:
    result = runner.invoke(app, ["ping", "-R", repository, "-m", "ping"])

    assert result.exit_code == 2
    assert "--repository:" in result.output
    assert fake_gate.requests == []


def test_caller_token_flag_is_gone() -> None:
    result = runner.invoke(app, ["ping", "-R", "a/b", "-m", "ping", "--caller-token", "x"])

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_output_is_sanitised(fake_gate: FakeTokenProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    def hostile(api: GitHubApi, request: VerbRequest) -> VerbResult:
        return VerbResult(VerbOutcome.OK, "pong\n::set-env name=PATH::/evil\n*bold*")

    tokens = FakeTokenProvider()
    factory = FakeApiFactory({".github/lktec-bot.yml": VALID})
    monkeypatch.setattr(
        "lktec_bot.cli.build_gate",
        lambda env=None: Gate(tokens, factory, {Verb.PING: hostile}),
    )

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert "ok: pong : :set-env name=PATH: :/evil *bold*" in lines
    assert not any(line.startswith("::") for line in lines)
    assert "pong : :set-env name=PATH: :/evil \\*bold\\*" in result.output  # the summary line


def test_tokens_are_masked_in_actions_and_never_printed_otherwise(
    fake_gate: FakeTokenProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[:2] == ["::add-mask::tok-0", "::add-mask::tok-1"]
    assert all("tok-" not in line for line in lines[2:])

    monkeypatch.delenv("GITHUB_ACTIONS")
    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert "tok-" not in result.output
    assert "add-mask" not in result.output


def test_uncaught_error_output_never_contains_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_pem = PEM
    secret_token = "ghs_do_not_print_me"

    def broken_build_gate(env: Mapping[str, str] | None = None) -> Gate:
        private_key = secret_pem  # locals a traceback with variables would reveal
        token = secret_token
        raise RuntimeError(f"wiring failed ({len(private_key)} + {len(token)} bytes of secrets)")

    monkeypatch.setattr("lktec_bot.cli.build_gate", broken_build_gate)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test

    with pytest.raises(RuntimeError) as excinfo:
        app(["ping", "-R", "acme/widgets", "-m", "ping"], standalone_mode=False)
    typer.main.except_hook(type(excinfo.value), excinfo.value, excinfo.value.__traceback__)

    captured = capsys.readouterr()
    assert "wiring failed" in captured.err
    assert secret_token not in captured.out + captured.err
    assert "BEGIN PRIVATE KEY" not in captured.out + captured.err


# -- wiring --------------------------------------------------------------------------------


def test_build_gate_wires_production_adapters_from_the_environment() -> None:
    env = {
        **APP_ENV,
        "LKTEC_BOT_API_URL": "https://ghe.example/api/v3/",
        "LKTEC_BOT_CALLER_TOKEN": "c",
    }

    gate = build_gate(env)

    assert isinstance(gate, Gate)
    assert isinstance(gate._tokens, AppTokenProvider)
    assert gate._tokens.base_url == "https://ghe.example/api/v3"
    api = gate._api_factory("t")
    assert isinstance(api, HttpGitHubApi)
    assert api.base_url == "https://ghe.example/api/v3"
    assert gate._caller_token == "c"
    gate.close()


def test_build_gate_defaults_to_github_com_and_no_caller_token() -> None:
    gate = build_gate({**APP_ENV, "LKTEC_BOT_CALLER_TOKEN": "  "})

    assert isinstance(gate._tokens, AppTokenProvider)
    assert gate._tokens.base_url == "https://api.github.com"
    assert gate._caller_token is None
    gate.close()


def test_token_provider_selection() -> None:
    assert isinstance(
        token_provider_from_env({**APP_ENV, "LKTEC_BOT_TOKEN_PROVIDER": " app "}), AppTokenProvider
    )

    with pytest.raises(ConfigError, match="'bogus' is not a known token provider \\(known: app\\)"):
        token_provider_from_env({**APP_ENV, "LKTEC_BOT_TOKEN_PROVIDER": "bogus"})


def test_unknown_token_provider_is_a_usage_error_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LKTEC_BOT_TOKEN_PROVIDER", "bogus")

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "ping"])

    assert result.exit_code == 2
    assert "LKTEC_BOT_TOKEN_PROVIDER='bogus' is not a known token provider" in result.output
