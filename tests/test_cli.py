from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lktec_bot import __version__
from lktec_bot.application.gate import Gate
from lktec_bot.cli import app
from tests.fakes import FakeApiFactory, FakeTokenProvider

runner = CliRunner()

VALID = "version: 1\nmodules:\n  automerge:\n    enabled: true\n"


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_policy_validate_ok(tmp_path: Path) -> None:
    policy = tmp_path / "lktec-bot.yml"
    policy.write_text(VALID, encoding="utf-8")

    result = runner.invoke(app, ["policy", "validate", str(policy)])

    assert result.exit_code == 0
    assert "valid: version 1, enabled modules: automerge" in result.output


def test_policy_validate_reports_every_reason(tmp_path: Path) -> None:
    policy = tmp_path / "lktec-bot.yml"
    policy.write_text("version: 3\nmodules:\n  automerge:\n    nope: 1\n", encoding="utf-8")

    result = runner.invoke(app, ["policy", "validate", str(policy)])

    assert result.exit_code == 1
    assert "invalid: version: Input should be 1" in result.output
    assert "invalid: modules.automerge.nope: Extra inputs are not permitted" in result.output


def test_policy_check_allowed(tmp_path: Path) -> None:
    policy = tmp_path / "lktec-bot.yml"
    policy.write_text(VALID, encoding="utf-8")

    result = runner.invoke(
        app,
        ["policy", "check", "--file", str(policy), "--module", "automerge", "--verb", "approve"],
    )

    assert result.exit_code == 0
    assert "approve for automerge: allowed" in result.output


def test_policy_check_denied(tmp_path: Path) -> None:
    policy = tmp_path / "lktec-bot.yml"
    policy.write_text(VALID, encoding="utf-8")

    result = runner.invoke(
        app, ["policy", "check", "-f", str(policy), "-m", "release", "-v", "release"]
    )

    assert result.exit_code == 1
    assert "release for release: denied" in result.output
    assert "not declared" in result.output


def test_policy_check_without_file_simulates_a_missing_policy() -> None:
    result = runner.invoke(app, ["policy", "check", "-m", "automerge", "-v", "ping"])

    assert result.exit_code == 1
    assert "missing" in result.output


def test_policy_check_invalid_file(tmp_path: Path) -> None:
    policy = tmp_path / "lktec-bot.yml"
    policy.write_text("version: 1\nextra: 1\n", encoding="utf-8")

    result = runner.invoke(app, ["policy", "check", "-f", str(policy), "-m", "a", "-v", "ping"])

    assert result.exit_code == 1
    assert "denied" in result.output
    assert "extra: Extra inputs are not permitted" in result.output


def test_policy_check_rejects_unknown_verb() -> None:
    result = runner.invoke(app, ["policy", "check", "-m", "automerge", "-v", "merge"])

    assert result.exit_code == 2


def test_ping_command_runs_through_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tokens = FakeTokenProvider()
    factory = FakeApiFactory({".github/lktec-bot.yml": VALID})
    from lktec_bot.application.verbs import HANDLERS

    def fake_gate(caller_token: str | None = None) -> Gate:
        return Gate(tokens, factory, HANDLERS, caller_token=caller_token)  # type: ignore[arg-type]

    monkeypatch.setattr("lktec_bot.cli.build_gate", fake_gate)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    result = runner.invoke(app, ["ping", "-R", "acme/widgets", "-m", "automerge"])

    assert result.exit_code == 0, result.output
    assert "ok: pong from acme/widgets" in result.output
    assert "`ping`" in summary.read_text(encoding="utf-8")
    assert tokens.requests[-1][2] == {"metadata": "read"}
    assert all(token.revoked for token in tokens.minted)


def test_ping_command_denied_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = FakeTokenProvider()
    factory = FakeApiFactory({})
    from lktec_bot.application.verbs import HANDLERS

    def fake_gate(caller_token: str | None = None) -> Gate:
        return Gate(tokens, factory, HANDLERS, caller_token=caller_token)  # type: ignore[arg-type]

    monkeypatch.setattr("lktec_bot.cli.build_gate", fake_gate)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    result = runner.invoke(app, ["ping", "--repository", "acme/widgets", "--module", "automerge"])

    assert result.exit_code == 1
    assert "denied:" in result.output
    assert "missing" in result.output


def test_ping_command_rejects_bad_repository() -> None:
    result = runner.invoke(app, ["ping", "-R", "no-slash", "-m", "automerge"])

    assert result.exit_code == 2
    assert "owner/name" in result.output


def test_build_gate_wires_production_adapters() -> None:
    from lktec_bot.cli import build_gate

    gate = build_gate("caller")

    assert isinstance(gate, Gate)
