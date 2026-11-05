"""Verb registry. Adding a verb is one new file in this package.

A verb module declares its handler and its grants together::

    @register(Verb.APPROVE, grants=frozenset({Grant(Permission.PULL_REQUESTS, "write")}))
    def approve(api: GitHubApi, request: VerbRequest) -> VerbResult: ...

The canonical permission table stays in ``lktec_bot/domain/permissions.py`` so the footprint
of every planned verb is reviewed in one place; ``register`` refuses a handler whose declared
grants differ from that table, so the two cannot drift apart. :func:`load_verbs` imports every
module of this package with ``pkgutil`` and returns the registry; nothing is imported at
package import time, which is what keeps the import graph acyclic.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lktec_bot.application.gate import VerbHandler
from lktec_bot.domain.permissions import Grant, Target, Verb, permissions_for

# Either one grant set, or one per target for the verbs whose permission depends on it.
GrantSpec = frozenset[Grant] | Mapping[Target, frozenset[Grant]]


@dataclass(frozen=True, slots=True)
class VerbSpec:
    verb: Verb
    handler: VerbHandler
    grants: GrantSpec


REGISTRY: dict[Verb, VerbSpec] = {}


def register(verb: Verb, *, grants: GrantSpec) -> Callable[[VerbHandler], VerbHandler]:
    """Register ``handler`` for ``verb``; ``grants`` must equal the domain table's entry."""

    def decorator(handler: VerbHandler) -> VerbHandler:
        if verb in REGISTRY:
            raise ValueError(f"verb {verb.value!r} is already registered")
        _check_grants(verb, grants)
        REGISTRY[verb] = VerbSpec(verb, handler, grants)
        return handler

    return decorator


def load_verbs() -> Mapping[Verb, VerbSpec]:
    """Import every verb module once so each ``@register`` runs, then return the registry."""
    for info in pkgutil.iter_modules(__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
    return REGISTRY


def handlers() -> dict[Verb, VerbHandler]:
    """The handler map the gate takes."""
    return {verb: spec.handler for verb, spec in load_verbs().items()}


def _check_grants(verb: Verb, grants: GrantSpec) -> None:
    targets: list[Target | None] = list(Target) if verb is Verb.COMMENT else [None]
    expected: dict[Target | None, frozenset[Grant]] = {
        target: permissions_for(verb, target) for target in targets
    }
    declared: dict[Target | None, frozenset[Grant]] = (
        {target: grant_set for target, grant_set in grants.items()}
        if isinstance(grants, Mapping)
        else {None: grants}
    )
    if declared != expected:
        raise ValueError(
            f"verb {verb.value!r} declares grants {_render(declared)} but the table in "
            f"domain/permissions.py says {_render(expected)}"
        )


def _render(grants: Mapping[Target | None, frozenset[Grant]]) -> str:
    parts = []
    for target, grant_set in sorted(grants.items(), key=lambda item: str(item[0])):
        names = ", ".join(sorted(str(grant) for grant in grant_set))
        parts.append(f"{target.value}: {names}" if target is not None else names)
    return "; ".join(parts)


__all__ = ["REGISTRY", "GrantSpec", "VerbSpec", "handlers", "load_verbs", "register"]
