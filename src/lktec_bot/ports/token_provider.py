"""Short-lived, narrowed tokens for one repository."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class Token(Protocol):
    """An installation access token. ``revoke`` is idempotent and never raises after success."""

    @property
    def value(self) -> str: ...

    @property
    def permissions(self) -> Mapping[str, str]: ...

    def revoke(self) -> None: ...


class TokenProvider(Protocol):
    def token_for(self, owner: str, repo: str, permissions: Mapping[str, str]) -> Token:
        """Mint a token valid for exactly ``owner/repo`` with exactly ``permissions``."""
        ...
