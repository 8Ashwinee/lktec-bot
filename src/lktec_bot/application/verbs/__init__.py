"""Verb registry. A verb module registers its handler with :func:`register`; the CLI and the
gate look handlers up here by :class:`~lktec_bot.domain.permissions.Verb`."""

from __future__ import annotations

from collections.abc import Callable

from lktec_bot.application.gate import VerbHandler
from lktec_bot.domain.permissions import Verb

HANDLERS: dict[Verb, VerbHandler] = {}


def register(verb: Verb) -> Callable[[VerbHandler], VerbHandler]:
    def decorator(handler: VerbHandler) -> VerbHandler:
        if verb in HANDLERS:
            raise ValueError(f"verb {verb.value!r} is already registered")
        HANDLERS[verb] = handler
        return handler

    return decorator


# Importing a verb module is what registers it. Add a new verb by adding its import here.
from lktec_bot.application.verbs import ping as _ping  # noqa: E402

__all__ = ["HANDLERS", "register"]
_ = _ping
