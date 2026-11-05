"""The per-repository policy file ``.github/lktec-bot.yml`` and the decisions made from it.

A repository opts in to the bot by committing this file. Everything is denied unless the file
exists, parses, and enables the module a verb belongs to. Unknown keys are errors rather than
silently ignored, so a typo in ``enabled`` can never open a door by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_core import InitErrorDetails

from lktec_bot.domain.permissions import Verb

POLICY_PATH = ".github/lktec-bot.yml"


class ModuleConfig(BaseModel):
    """Base shape of every module entry: nothing but ``enabled`` until a module adds options."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class AutomergeConfig(ModuleConfig):
    """Options of the ``automerge`` module."""

    exclude: list[str] = []
    majors: Literal["manual", "auto"] = "manual"


# Module name -> the model that validates its options. A new module registers itself here;
# a module that is not registered may only carry ``enabled`` (``ModuleConfig`` forbids more).
MODULE_MODELS: dict[str, type[ModuleConfig]] = {
    "automerge": AutomergeConfig,
}


class Policy(BaseModel):
    """Validated content of ``.github/lktec-bot.yml``."""

    model_config = ConfigDict(extra="forbid")

    path: ClassVar[str] = POLICY_PATH

    version: Literal[1]
    modules: dict[str, ModuleConfig] = {}

    @field_validator("modules", mode="before")
    @classmethod
    def _typed_modules(cls, raw: object) -> object:
        """Validate each module with its registered model, keeping the module name in errors."""
        if not isinstance(raw, dict):
            return raw
        typed: dict[str, Any] = {}
        line_errors: list[InitErrorDetails] = []
        for name, options in raw.items():
            model = MODULE_MODELS.get(str(name), ModuleConfig)
            try:
                typed[str(name)] = model.model_validate(options if options is not None else {})
            except ValidationError as exc:
                for error in exc.errors(include_url=False):
                    detail: InitErrorDetails = {
                        "type": error["type"],
                        "loc": (str(name), *error["loc"]),
                        "input": error["input"],
                    }
                    if "ctx" in error:
                        detail["ctx"] = error["ctx"]
                    line_errors.append(detail)
        if line_errors:
            raise ValidationError.from_exception_data("modules", line_errors)
        return typed

    def allows(self, module: str, verb: Verb | str) -> Decision:
        """Decide whether ``verb`` may run on behalf of ``module`` under this policy."""
        verb_name = _verb_name(verb)
        if verb_name is None:
            return Decision(False, module, str(verb), f"unknown verb {verb!s}")
        config = self.modules.get(module)
        if config is None:
            return Decision(
                False, module, verb_name, f"module {module!r} is not declared in {self.path}"
            )
        if not config.enabled:
            return Decision(
                False, module, verb_name, f"module {module!r} is disabled in {self.path}"
            )
        return Decision(True, module, verb_name, f"module {module!r} is enabled in {self.path}")


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of a policy check, always with a reason a human can read in a job summary."""

    allowed: bool
    module: str
    verb: str
    reason: str

    def __str__(self) -> str:
        state = "allowed" if self.allowed else "denied"
        return f"{self.verb} for {self.module}: {state} ({self.reason})"


class PolicyError(ValueError):
    """The policy file exists but is not valid. ``reasons`` lists every problem found."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def load_policy(text: str) -> Policy:
    """Parse and validate policy YAML. Raises :class:`PolicyError` with readable reasons."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError([f"not valid YAML: {exc}"]) from exc
    if raw is None:
        raise PolicyError(["the policy file is empty"])
    if not isinstance(raw, dict):
        raise PolicyError(["the policy file must be a mapping with `version` and `modules`"])
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(_format_errors(exc)) from exc


def decide(policy: Policy | None, module: str, verb: Verb | str) -> Decision:
    """Decide for a possibly missing policy: no file means everything is denied."""
    if policy is None:
        return Decision(False, module, str(verb), f"{POLICY_PATH} is missing in the repository")
    return policy.allows(module, verb)


def _verb_name(verb: Verb | str) -> str | None:
    try:
        return Verb(verb).value
    except ValueError:
        return None


def _format_errors(exc: ValidationError) -> list[str]:
    reasons: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        reasons.append(f"{location}: {error['msg']}")
    return reasons
