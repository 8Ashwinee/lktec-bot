from __future__ import annotations

import pytest

from lktec_bot.domain.permissions import Verb
from lktec_bot.domain.policy import (
    POLICY_PATH,
    AutomergeConfig,
    ModuleConfig,
    Policy,
    PolicyError,
    decide,
    load_policy,
)

VALID = """
version: 1
modules:
  automerge:
    enabled: true
    exclude: ["some-package"]
    majors: auto
  release:
    enabled: false
"""


def test_valid_policy_parses_with_typed_modules() -> None:
    policy = load_policy(VALID)

    assert policy.version == 1
    automerge = policy.modules["automerge"]
    assert isinstance(automerge, AutomergeConfig)
    assert automerge.enabled is True
    assert automerge.exclude == ["some-package"]
    assert automerge.majors == "auto"
    release = policy.modules["release"]
    assert type(release) is ModuleConfig
    assert release.enabled is False


def test_defaults_are_closed() -> None:
    policy = load_policy("version: 1\nmodules:\n  automerge: {}\n")

    automerge = policy.modules["automerge"]
    assert isinstance(automerge, AutomergeConfig)
    assert automerge.enabled is False
    assert automerge.exclude == []
    assert automerge.majors == "manual"


def test_module_without_options_is_accepted() -> None:
    policy = load_policy("version: 1\nmodules:\n  automerge:\n")

    assert policy.modules["automerge"].enabled is False


def test_missing_modules_key_means_no_modules() -> None:
    assert load_policy("version: 1\n").modules == {}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("version: 1\nunknown: true\n", "unknown: Extra inputs are not permitted"),
        (
            "version: 1\nmodules:\n  automerge:\n    enabled: true\n    typo: 1\n",
            "typo: Extra inputs are not permitted",
        ),
        (
            "version: 1\nmodules:\n  release:\n    enabled: true\n    channel: stable\n",
            "channel: Extra inputs are not permitted",
        ),
        ("version: 2\n", "version: Input should be 1"),
        ("modules: {}\n", "version: Field required"),
        (
            "version: 1\nmodules:\n  automerge:\n    majors: sometimes\n",
            "majors: Input should be 'manual' or 'auto'",
        ),
        ("version: 1\nmodules:\n  automerge:\n    enabled: maybe\n", "enabled: Input should be"),
    ],
)
def test_invalid_policy_reports_readable_reasons(text: str, fragment: str) -> None:
    with pytest.raises(PolicyError) as excinfo:
        load_policy(text)

    assert any(fragment in reason for reason in excinfo.value.reasons), excinfo.value.reasons
    assert str(excinfo.value)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "empty"),
        ("- just\n- a list\n", "must be a mapping"),
        ("version: [1\n", "not valid YAML"),
    ],
)
def test_structurally_broken_files(text: str, fragment: str) -> None:
    with pytest.raises(PolicyError) as excinfo:
        load_policy(text)

    assert fragment in excinfo.value.reasons[0]


def test_enabled_module_allows_every_verb() -> None:
    policy = load_policy(VALID)

    decision = policy.allows("automerge", Verb.ENABLE_AUTOMERGE)

    assert decision.allowed is True
    assert decision.module == "automerge"
    assert decision.verb == "enable-automerge"
    assert "enabled" in decision.reason
    assert "allowed" in str(decision)


def test_disabled_module_is_denied() -> None:
    decision = load_policy(VALID).allows("release", "release")

    assert decision.allowed is False
    assert "disabled" in decision.reason
    assert POLICY_PATH in decision.reason


def test_undeclared_module_is_denied() -> None:
    decision = load_policy(VALID).allows("deploy", Verb.DEPLOYMENT)

    assert decision.allowed is False
    assert "not declared" in decision.reason


def test_unknown_verb_is_denied() -> None:
    decision = load_policy(VALID).allows("automerge", "merge")

    assert decision.allowed is False
    assert "unknown verb" in decision.reason


def test_missing_file_denies_everything() -> None:
    decision = decide(None, "automerge", Verb.APPROVE)

    assert decision.allowed is False
    assert "missing" in decision.reason
    assert POLICY_PATH in decision.reason


def test_decide_delegates_to_policy() -> None:
    policy = Policy(version=1, modules={"automerge": AutomergeConfig(enabled=True)})

    assert decide(policy, "automerge", Verb.APPROVE).allowed is True
