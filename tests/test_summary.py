from __future__ import annotations

import io
from pathlib import Path

from lktec_bot.summary import write_summary


def test_appends_to_the_step_summary_when_set(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n", encoding="utf-8")

    write_summary("first", env={"GITHUB_STEP_SUMMARY": str(summary)})
    write_summary("second\n", env={"GITHUB_STEP_SUMMARY": str(summary)})

    assert summary.read_text(encoding="utf-8") == "existing\nfirst\nsecond\n"


def test_prints_when_not_in_actions() -> None:
    out = io.StringIO()

    write_summary("hello", env={}, out=out)

    assert out.getvalue() == "hello\n"


def test_empty_summary_variable_counts_as_unset() -> None:
    out = io.StringIO()

    write_summary("x\n", env={"GITHUB_STEP_SUMMARY": ""}, out=out)

    assert out.getvalue() == "x\n"
