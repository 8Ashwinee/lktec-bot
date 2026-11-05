from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from lktec_bot.application.modules import (
    MODULES,
    AutomergeOptions,
    ModuleSpec,
    authorize,
    policy_problems,
    register_module,
    validate_policy,
)
from lktec_bot.domain.permissions import Verb
from lktec_bot.domain.policy import POLICY_PATH, PolicyError, load_policy

AUTOMERGE = "version: 1\nmodules:\n  automerge:\n    enabled: true\n"
PING = "version: 1\nmodules:\n  ping:\n    enabled: true\n"


def test_registry_declares_the_shipped_modules() -> None:
    assert set(MODULES) == {"automerge", "ping"}
    assert MODULES["automerge"].verbs == {
        Verb.APPROVE,
        Verb.ENABLE_AUTOMERGE,
        Verb.UPDATE_BRANCH,
        Verb.COMMENT,
    }
    assert MODULES["automerge"].options is AutomergeOptions
    assert MODULES["ping"].verbs == {Verb.PING}
    assert MODULES["ping"].options is None
    assert MODULES["automerge"].verb_names() == "approve, comment, enable-automerge, update-branch"


def test_register_module_refuses_duplicates() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_module("ping", {Verb.PING})


def test_register_module_refuses_an_options_model_that_allows_extra_keys() -> None:
    class Loose(BaseModel):
        model_config = ConfigDict(extra="ignore")

    with pytest.raises(ValueError, match="extra='forbid'"):
        register_module("loose", {Verb.PING}, Loose)
    assert "loose" not in MODULES


def test_option_problems_for_a_module_with_options() -> None:
    spec = MODULES["automerge"]

    assert spec.option_problems({}) == []
    assert spec.option_problems({"exclude": ["a"], "majors": "auto"}) == []
    assert spec.option_problems({"majors": "sometimes"}) == [
        "majors: Input should be 'manual' or 'auto'"
    ]
    assert spec.option_problems({"typo": 1}) == ["typo: Extra inputs are not permitted"]


def test_option_problems_for_a_module_without_options() -> None:
    spec = MODULES["ping"]

    assert spec.option_problems({}) == []
    assert spec.option_problems({"b": 1, "a": 2}) == [
        "a: module 'ping' takes no options",
        "b: module 'ping' takes no options",
    ]


def test_validate_policy_accepts_a_good_file() -> None:
    policy = validate_policy(AUTOMERGE + "    exclude: [x]\n    majors: auto\n  ping: {}\n")

    assert policy.modules["automerge"].enabled is True
    assert policy.modules["automerge"].options == {"exclude": ["x"], "majors": "auto"}
    assert policy.modules["ping"].enabled is False


def test_validate_policy_rejects_an_unknown_module() -> None:
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(PING + "  mystery:\n    enabled: true\n")

    assert excinfo.value.reasons == ["modules.mystery: unknown module (known: automerge, ping)"]


def test_validate_policy_rejects_bad_options_with_the_module_in_the_path() -> None:
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(AUTOMERGE + "    majors: sometimes\n    typo: 1\n  ping:\n    x: 1\n")

    assert excinfo.value.reasons == [
        "modules.automerge.majors: Input should be 'manual' or 'auto'",
        "modules.automerge.typo: Extra inputs are not permitted",
        "modules.ping.x: module 'ping' takes no options",
    ]


def test_validate_policy_passes_structural_errors_through() -> None:
    with pytest.raises(PolicyError, match="version: Field required"):
        validate_policy("modules: {}\n")


def test_policy_problems_with_a_custom_registry() -> None:
    registry = {"mine": ModuleSpec("mine", frozenset({Verb.PING}))}
    policy = load_policy("version: 1\nmodules:\n  mine: {}\n  automerge: {}\n")

    assert policy_problems(policy, registry) == ["modules.automerge: unknown module (known: mine)"]
    assert policy_problems(load_policy("version: 1\n"), {}) == []


def test_authorize_denies_an_unknown_module_with_or_without_a_policy() -> None:
    for policy in (None, load_policy(PING)):
        decision = authorize(policy, "mystery", Verb.PING)

        assert decision.allowed is False
        assert decision.reason == "module 'mystery' is not known (known: automerge, ping)"
        assert decision.module == "mystery"
        assert decision.verb == "ping"


def test_authorize_denies_a_missing_policy() -> None:
    decision = authorize(None, "ping", Verb.PING)

    assert decision.allowed is False
    assert "missing" in decision.reason


def test_authorize_denies_an_undeclared_module() -> None:
    decision = authorize(load_policy(AUTOMERGE), "ping", Verb.PING)

    assert decision.allowed is False
    assert "not declared" in decision.reason


def test_authorize_denies_a_disabled_module() -> None:
    decision = authorize(load_policy("version: 1\nmodules:\n  ping: {}\n"), "ping", Verb.PING)

    assert decision.allowed is False
    assert "disabled" in decision.reason


def test_authorize_denies_a_verb_outside_the_module_set() -> None:
    decision = authorize(load_policy(AUTOMERGE), "automerge", Verb.RELEASE)

    assert decision.allowed is False
    assert decision.reason == (
        "module 'automerge' may not run 'release'; its verbs are: "
        "approve, comment, enable-automerge, update-branch"
    )


def test_authorize_allows_a_listed_verb_of_an_enabled_module() -> None:
    decision = authorize(load_policy(AUTOMERGE), "automerge", Verb.ENABLE_AUTOMERGE)

    assert decision.allowed is True
    assert decision.reason == (
        f"module 'automerge' is enabled in {POLICY_PATH} and may run 'enable-automerge'"
    )
    assert "allowed" in str(decision)


def test_authorize_with_a_custom_registry() -> None:
    registry = {"mine": ModuleSpec("mine", frozenset({Verb.RELEASE}))}
    policy = load_policy("version: 1\nmodules:\n  mine:\n    enabled: true\n")

    assert authorize(policy, "mine", Verb.RELEASE, registry).allowed is True
    assert authorize(policy, "mine", Verb.PING, registry).allowed is False
    assert "known: none" in authorize(policy, "mine", Verb.PING, {}).reason
