"""The minimal GitHub surface the verbs need. Every method is one API call."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal, Protocol

ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
MergeMethod = Literal["MERGE", "SQUASH", "REBASE"]


class GitHubApi(Protocol):
    """A GitHub client bound to one token. Adapters decide how the calls are made."""

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str | None:
        """Return the decoded text of a file, or ``None`` when it does not exist."""
        ...

    def create_review(
        self, owner: str, repo: str, number: int, event: ReviewEvent, body: str = ""
    ) -> str:
        """Submit a pull request review and return its HTML URL."""
        ...

    def enable_auto_merge(self, pull_request_id: str, merge_method: MergeMethod) -> str:
        """Enable auto-merge on a pull request (GraphQL node id) and return the PR URL."""
        ...

    def update_branch(self, owner: str, repo: str, number: int, expected_head_sha: str) -> str:
        """Update a pull request branch with its base and return the API's message."""
        ...

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        """Comment on an issue or pull request and return the comment's HTML URL."""
        ...

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run a GraphQL query and return its ``data`` object."""
        ...


# Builds a client for a freshly minted token; the gate calls it once per token.
GitHubApiFactory = Callable[[str], GitHubApi]
