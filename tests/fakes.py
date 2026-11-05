"""In-memory ports for the gate tests. They record every call so tests can assert on them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from lktec_bot.ports.github_api import MergeMethod, ReviewEvent
from lktec_bot.ports.token_provider import TokenError


@dataclass
class FakeToken:
    value: str
    permissions: Mapping[str, str] | None
    repositories: frozenset[str] | None
    revoked: bool = False
    revoke_calls: int = 0
    failing_revokes: int = 0  # how many revoke calls raise before one succeeds

    def revoke(self) -> None:
        self.revoke_calls += 1
        if self.revoke_calls <= self.failing_revokes:
            raise TokenError(f"revoking the token failed: attempt {self.revoke_calls}")
        self.revoked = True


@dataclass
class FakeTokenProvider:
    """Mints a distinct token per call and keeps them all for inspection.

    By default a token reports exactly the requested permissions plus ``metadata:read`` and
    the one target repository, as GitHub does. ``permissions_by_mint`` and
    ``repositories_by_mint`` override what the n-th minted token reports (``None`` means the
    provider reports nothing); ``failing_revokes`` makes the first n revoke calls of every
    token raise.
    """

    minted: list[FakeToken] = field(default_factory=list)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    fail_with: Exception | None = None
    permissions_by_mint: dict[int, Mapping[str, str] | None] = field(default_factory=dict)
    repositories_by_mint: dict[int, frozenset[str] | None] = field(default_factory=dict)
    failing_revokes: int = 0
    closed: bool = False

    def token_for(self, owner: str, repo: str, permissions: Mapping[str, str]) -> FakeToken:
        self.requests.append((owner, repo, dict(permissions)))
        if self.fail_with is not None:
            raise self.fail_with
        index = len(self.minted)
        granted = self.permissions_by_mint.get(index, {"metadata": "read", **permissions})
        repositories = self.repositories_by_mint.get(index, frozenset({repo}))
        token = FakeToken(
            value=f"tok-{index}",
            permissions=None if granted is None else dict(granted),
            repositories=repositories,
            failing_revokes=self.failing_revokes,
        )
        self.minted.append(token)
        return token

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeGitHubApi:
    """Serves files from a dict and records which token was used for which call."""

    token: str
    files: Mapping[str, str]
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    closed: bool = False

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str | None:
        self.calls.append(("get_file_content", (owner, repo, path, ref)))
        return self.files.get(path)

    def create_review(
        self, owner: str, repo: str, number: int, event: ReviewEvent, body: str = ""
    ) -> str:
        self.calls.append(("create_review", (owner, repo, number, event, body)))
        return f"https://github.com/{owner}/{repo}/pull/{number}#pullrequestreview-1"

    def enable_auto_merge(
        self, owner: str, repo: str, number: int, merge_method: MergeMethod
    ) -> str:
        self.calls.append(("enable_auto_merge", (owner, repo, number, merge_method)))
        return f"https://github.com/{owner}/{repo}/pull/{number}"

    def update_branch(self, owner: str, repo: str, number: int, expected_head_sha: str) -> str:
        self.calls.append(("update_branch", (owner, repo, number, expected_head_sha)))
        return "Updating pull request branch."

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        self.calls.append(("create_issue_comment", (owner, repo, number, body)))
        return f"https://github.com/{owner}/{repo}/issues/{number}#issuecomment-1"

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("graphql", (query, dict(variables or {}))))
        return {}

    def close(self) -> None:
        self.closed = True


class FakeApiFactory:
    """Builds FakeGitHubApi instances bound to a token and remembers every one."""

    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = files
        self.created: list[FakeGitHubApi] = []

    def __call__(self, token: str) -> FakeGitHubApi:
        api = FakeGitHubApi(token=token, files=self.files)
        self.created.append(api)
        return api
