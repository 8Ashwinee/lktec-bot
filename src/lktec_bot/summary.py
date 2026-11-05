"""Write markdown to the GitHub Actions job summary, or to stdout outside Actions."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

SUMMARY_VAR = "GITHUB_STEP_SUMMARY"


def write_summary(
    markdown: str,
    *,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    environment = env if env is not None else os.environ
    text = markdown if markdown.endswith("\n") else markdown + "\n"
    path = environment.get(SUMMARY_VAR)
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text)
        return
    (out or sys.stdout).write(text)
