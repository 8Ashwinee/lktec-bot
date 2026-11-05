"""The minimal GitHub surface the verbs need. Every method is one API call (or two, where a
node id has to be resolved first, or a few, where a list is paginated)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
MergeMethod = Literal["MERGE", "SQUASH", "REBASE"]


@dataclass(frozen=True, slots=True)
class PullRequest:
    """What the verbs need to know about a pull request before they touch it."""

    number: int
    html_url: str
    state: str  # "open" or "closed"
    draft: bool
    head_sha: str
    auto_merge_method: MergeMethod | None = None  # set when auto-merge is already enabled

    @property
    def open(self) -> bool:
        return self.state == "open"


@dataclass(frozen=True, slots=True)
class Review:
    """One submitted pull request review."""

    user_login: str
    state: str  # "APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"
    commit_id: str
    html_url: str


@dataclass(frozen=True, slots=True)
class IssueComment:
    """One comment on an issue or pull request conversation."""

    id: int
    user_login: str
    user_type: str  # "User" or "Bot"
    body: str
    html_url: str


class BranchUpdate(StrEnum):
    """What ``update_branch`` achieved: the update was queued, or there was nothing to do."""

    ACCEPTED = "accepted"
    UP_TO_DATE = "up-to-date"


class GitHubApi(Protocol):
    """A GitHub client bound to one token. Adapters decide how the calls are made."""

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str | None:
        """Return the decoded text of a file, or ``None`` when it does not exist.

        Without ``ref`` the file is read from the repository's default branch.
        """
        ...

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """Fetch the state of pull request ``number``."""
        ...

    def list_reviews(self, owner: str, repo: str, number: int) -> Sequence[Review]:
        """Every submitted review on pull request ``number``, oldest first."""
        ...

    def create_review(
        self, owner: str, repo: str, number: int, event: ReviewEvent, body: str = ""
    ) -> str:
        """Submit a pull request review and return its HTML URL."""
        ...

    def enable_auto_merge(
        self, owner: str, repo: str, number: int, merge_method: MergeMethod
    ) -> str:
        """Enable auto-merge on pull request ``number`` and return the PR URL.

        The adapter resolves the pull request's GraphQL node id itself.
        """
        ...

    def update_branch(
        self, owner: str, repo: str, number: int, expected_head_sha: str
    ) -> BranchUpdate:
        """Update a pull request branch with its base.

        ``ACCEPTED`` when GitHub queued the merge commit, ``UP_TO_DATE`` when the branch already
        contains the base; anything else is an error.
        """
        ...

    def list_issue_comments(self, owner: str, repo: str, number: int) -> Sequence[IssueComment]:
        """Every comment on issue or pull request ``number``, oldest first."""
        ...

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        """Comment on an issue or pull request and return the comment's HTML URL."""
        ...

    def update_issue_comment(self, owner: str, repo: str, comment_id: int, body: str) -> str:
        """Replace the body of an existing comment and return its HTML URL."""
        ...

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run a GraphQL query and return its ``data`` object."""
        ...

    def close(self) -> None:
        """Release the client's connections. Idempotent."""
        ...


# Builds a client for a freshly minted token; the gate calls it once per token and closes it.
GitHubApiFactory = Callable[[str], GitHubApi]
