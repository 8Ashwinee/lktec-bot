"""In-memory ports for the gate tests. They record every call so tests can assert on them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from lktec_bot.ports.github_api import MergeMethod, ReviewEvent


@dataclass
class FakeToken:
    value: str
    permissions: Mapping[str, str]
    revoked: bool = False
    revoke_calls: int = 0

    def revoke(self) -> None:
        self.revoke_calls += 1
        self.revoked = True


@dataclass
class FakeTokenProvider:
    """Mints a distinct token per call and keeps them all for inspection."""

    minted: list[FakeToken] = field(default_factory=list)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    fail_with: Exception | None = None

    def token_for(self, owner: str, repo: str, permissions: Mapping[str, str]) -> FakeToken:
        self.requests.append((owner, repo, dict(permissions)))
        if self.fail_with is not None:
            raise self.fail_with
        token = FakeToken(value=f"tok-{len(self.minted)}", permissions=dict(permissions))
        self.minted.append(token)
        return token


@dataclass
class FakeGitHubApi:
    """Serves files from a dict and records which token was used for which call."""

    token: str
    files: Mapping[str, str]
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

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

    def enable_auto_merge(self, pull_request_id: str, merge_method: MergeMethod) -> str:
        self.calls.append(("enable_auto_merge", (pull_request_id, merge_method)))
        return "https://github.com/o/r/pull/1"

    def update_branch(self, owner: str, repo: str, number: int, expected_head_sha: str) -> str:
        self.calls.append(("update_branch", (owner, repo, number, expected_head_sha)))
        return "Updating pull request branch."

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        self.calls.append(("create_issue_comment", (owner, repo, number, body)))
        return f"https://github.com/{owner}/{repo}/issues/{number}#issuecomment-1"

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("graphql", (query, dict(variables or {}))))
        return {}


class FakeApiFactory:
    """Builds FakeGitHubApi instances bound to a token and remembers every one."""

    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = files
        self.created: list[FakeGitHubApi] = []

    def __call__(self, token: str) -> FakeGitHubApi:
        api = FakeGitHubApi(token=token, files=self.files)
        self.created.append(api)
        return api
