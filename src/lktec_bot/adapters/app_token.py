"""Installation tokens minted from the App's private key, narrowed to one repo and one verb."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import jwt

from lktec_bot.adapters.github_http import API_VERSION, DEFAULT_BASE_URL, USER_AGENT
from lktec_bot.ports.key_source import AppCredentials, KeySource

# GitHub accepts at most 10 minutes; 9 minutes plus a 60 s backdated iat absorbs clock skew.
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60


class TokenError(RuntimeError):
    """The App could not be resolved on the repository or the token could not be minted."""


def build_app_jwt(credentials: AppCredentials, now: Callable[[], float] = time.time) -> str:
    """Build the RS256 JWT that authenticates as the App itself (not as an installation)."""
    issued_at = int(now())
    claims = {
        "iat": issued_at - JWT_BACKDATE_SECONDS,
        "exp": issued_at + JWT_LIFETIME_SECONDS,
        "iss": credentials.app_id,
    }
    return jwt.encode(claims, credentials.private_key_pem, algorithm="RS256")


class InstallationToken:
    """A minted token; ``revoke`` is idempotent so the gate can always call it in ``finally``."""

    def __init__(
        self,
        value: str,
        permissions: Mapping[str, str],
        expires_at: str,
        revoker: Callable[[str], None],
    ) -> None:
        self._value = value
        self._permissions = dict(permissions)
        self.expires_at = expires_at
        self._revoker = revoker
        self.revoked = False

    @property
    def value(self) -> str:
        return self._value

    @property
    def permissions(self) -> Mapping[str, str]:
        return self._permissions

    def revoke(self) -> None:
        if self.revoked:
            return
        self._revoker(self._value)
        self.revoked = True

    def __repr__(self) -> str:
        return f"InstallationToken(permissions={self._permissions!r}, revoked={self.revoked})"


class AppTokenProvider:
    """:class:`~lktec_bot.ports.token_provider.TokenProvider` backed by the GitHub App API."""

    def __init__(
        self,
        key_source: KeySource,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._key_source = key_source
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)
        self._now = now

    def token_for(self, owner: str, repo: str, permissions: Mapping[str, str]) -> InstallationToken:
        app_jwt = build_app_jwt(self._key_source.load(), self._now)
        installation_id = self._installation_id(app_jwt, owner, repo)
        response = self._client.post(
            f"{self._base_url}/app/installations/{installation_id}/access_tokens",
            headers=self._app_headers(app_jwt),
            json={"repositories": [repo], "permissions": dict(permissions)},
        )
        if response.status_code != 201:
            raise TokenError(
                f"minting a token for {owner}/{repo} failed: "
                f"{response.status_code} {response.text.strip()[:200]}"
            )
        payload: dict[str, Any] = response.json()
        return InstallationToken(
            value=str(payload["token"]),
            permissions=payload.get("permissions", dict(permissions)),
            expires_at=str(payload.get("expires_at", "")),
            revoker=self._revoke,
        )

    def _installation_id(self, app_jwt: str, owner: str, repo: str) -> int:
        response = self._client.get(
            f"{self._base_url}/repos/{owner}/{repo}/installation",
            headers=self._app_headers(app_jwt),
        )
        if response.status_code == 404:
            raise TokenError(f"the App is not installed on {owner}/{repo}")
        if not response.is_success:
            raise TokenError(
                f"resolving the installation on {owner}/{repo} failed: {response.status_code}"
            )
        return int(response.json()["id"])

    def _revoke(self, token: str) -> None:
        response = self._client.delete(
            f"{self._base_url}/installation/token",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
        )
        if response.status_code not in (204, 401):  # 401: already expired or revoked
            raise TokenError(f"revoking the token failed: {response.status_code}")

    @staticmethod
    def _app_headers(app_jwt: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
