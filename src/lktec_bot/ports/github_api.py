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
    title: str = ""
    body: str = ""
    author_login: str = ""  # ``dependabot[bot]`` for Dependabot, a user's login otherwise
    # GitHub's ``mergeable_state`` in upper case (``BEHIND``, ``DIRTY``, ``CLEAN``, ``BLOCKED``,
    # ``UNSTABLE``, ``UNKNOWN``, ...). Only the single-pull-request call reports it; GitHub
    # computes it lazily, so ``UNKNOWN`` means "ask again in a moment". Empty when not reported.
    merge_state: str = ""
    # The head branch name and the repository it lives in (``owner/name``; empty when the head
    # repository is gone). A fork head is a different repository than the pull request's own.
    head_ref: str = ""
    head_repo: str = ""

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


@dataclass(frozen=True, slots=True)
class CreatedCommit:
    """The commit ``create_commit_on_branch`` made."""

    oid: str
    url: str


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

    def list_pull_requests(self, owner: str, repo: str) -> Sequence[PullRequest]:
        """Every open pull request, oldest first. ``merge_state`` is not reported by a list."""
        ...

    def list_pull_request_files(self, owner: str, repo: str, number: int) -> Sequence[str]:
        """The paths pull request ``number`` changes (a renamed file contributes both paths)."""
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

    def get_branch_head(self, owner: str, repo: str, branch: str) -> str | None:
        """The commit id the branch points at, or ``None`` when the branch does not exist."""
        ...

    def create_commit_on_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        headline: str,
        body: str,
        expected_head_oid: str,
        additions: Mapping[str, bytes],
        deletions: Sequence[str],
    ) -> CreatedCommit:
        """Commit the changes onto ``branch`` through GraphQL ``createCommitOnBranch``.

        GitHub creates and signs the commit; it fails when the branch no longer points at
        ``expected_head_oid`` instead of overwriting a concurrent push. ``additions`` maps a
        repository path to the file's new content (the adapter encodes it).
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
