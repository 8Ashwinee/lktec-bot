"""In-memory ports for the gate tests. They record every call so tests can assert on them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from lktec_bot.ports.github_api import (
    BranchUpdate,
    IssueComment,
    MergeMethod,
    PullRequest,
    Review,
    ReviewEvent,
)
from lktec_bot.ports.token_provider import TokenError

BOT = "fake-app[bot]"


@dataclass
class FakeToken:
    value: str
    permissions: Mapping[str, str] | None
    repositories: frozenset[str] | None
    actor: str | None = BOT
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

    By default a token reports exactly the requested permissions plus ``metadata:read``, the
    one target repository and the actor ``fake-app[bot]``, as GitHub does.
    ``permissions_by_mint`` and ``repositories_by_mint`` override what the n-th minted token
    reports (``None`` means the provider reports nothing); ``failing_revokes`` makes the first
    n revoke calls of every token raise.
    """

    minted: list[FakeToken] = field(default_factory=list)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    fail_with: Exception | None = None
    permissions_by_mint: dict[int, Mapping[str, str] | None] = field(default_factory=dict)
    repositories_by_mint: dict[int, frozenset[str] | None] = field(default_factory=dict)
    actor: str | None = BOT
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
            actor=self.actor,
            failing_revokes=self.failing_revokes,
        )
        self.minted.append(token)
        return token

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeRepository:
    """The GitHub state the verbs act on, shared by every client a factory builds.

    ``comments`` and ``reviews`` hold what exists before the verb runs; the verbs' writes land
    in ``created_comments``, ``updated_comments``, ``reviews_created``, ``auto_merge`` and
    ``branch_updates``.
    """

    pull_requests: dict[int, PullRequest] = field(default_factory=dict)
    reviews: dict[int, list[Review]] = field(default_factory=dict)
    comments: dict[int, list[IssueComment]] = field(default_factory=dict)
    branch_update: BranchUpdate = BranchUpdate.ACCEPTED
    created_comments: list[tuple[int, str]] = field(default_factory=list)
    updated_comments: list[tuple[int, str]] = field(default_factory=list)
    reviews_created: list[tuple[int, str, str]] = field(default_factory=list)
    auto_merge: list[tuple[int, str]] = field(default_factory=list)
    branch_updates: list[tuple[int, str]] = field(default_factory=list)
    next_comment_id: int = 100
    # Changed files per pull request; what ``list_pull_request_files`` answers.
    pull_files: dict[int, list[str]] = field(default_factory=dict)
    # ``merge_state`` answers handed out one per ``get_pull_request`` call before the stored
    # state applies, to play GitHub's lazily computed UNKNOWN.
    merge_state_script: dict[int, list[str]] = field(default_factory=dict)
    # Every write in order (``approve #7``, ``enable-automerge #7``, ...), across all clients.
    log: list[str] = field(default_factory=list)


@dataclass
class FakeGitHubApi:
    """Serves files from a dict and records which token was used for which call."""

    token: str
    files: Mapping[str, str]
    state: FakeRepository = field(default_factory=FakeRepository)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    closed: bool = False

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str | None:
        self.calls.append(("get_file_content", (owner, repo, path, ref)))
        return self.files.get(path)

    def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        self.calls.append(("get_pull_request", (owner, repo, number)))
        try:
            pull_request = self.state.pull_requests[number]
        except KeyError:
            raise RuntimeError(f"GET pulls/{number} -> 404: Not Found") from None
        script = self.state.merge_state_script.get(number)
        if script:
            pull_request = replace(pull_request, merge_state=script.pop(0))
        return pull_request

    def list_pull_requests(self, owner: str, repo: str) -> list[PullRequest]:
        self.calls.append(("list_pull_requests", (owner, repo)))
        return [
            replace(pull_request, merge_state="")
            for _, pull_request in sorted(self.state.pull_requests.items())
            if pull_request.open
        ]

    def list_pull_request_files(self, owner: str, repo: str, number: int) -> list[str]:
        self.calls.append(("list_pull_request_files", (owner, repo, number)))
        return list(self.state.pull_files.get(number, []))

    def list_reviews(self, owner: str, repo: str, number: int) -> list[Review]:
        self.calls.append(("list_reviews", (owner, repo, number)))
        return list(self.state.reviews.get(number, []))

    def create_review(
        self, owner: str, repo: str, number: int, event: ReviewEvent, body: str = ""
    ) -> str:
        self.calls.append(("create_review", (owner, repo, number, event, body)))
        self.state.reviews_created.append((number, event, body))
        self.state.log.append(f"approve #{number}" if event == "APPROVE" else f"review #{number}")
        if event == "APPROVE":
            head = self.state.pull_requests[number].head_sha
            url = f"https://github.com/{owner}/{repo}/pull/{number}#pullrequestreview-1"
            self.state.reviews.setdefault(number, []).append(Review(BOT, "APPROVED", head, url))
        return f"https://github.com/{owner}/{repo}/pull/{number}#pullrequestreview-1"

    def enable_auto_merge(
        self, owner: str, repo: str, number: int, merge_method: MergeMethod
    ) -> str:
        self.calls.append(("enable_auto_merge", (owner, repo, number, merge_method)))
        self.state.auto_merge.append((number, merge_method))
        self.state.log.append(f"enable-automerge #{number} {merge_method.lower()}")
        pull_request = self.state.pull_requests[number]
        self.state.pull_requests[number] = replace(pull_request, auto_merge_method=merge_method)
        return f"https://github.com/{owner}/{repo}/pull/{number}"

    def update_branch(
        self, owner: str, repo: str, number: int, expected_head_sha: str
    ) -> BranchUpdate:
        self.calls.append(("update_branch", (owner, repo, number, expected_head_sha)))
        self.state.branch_updates.append((number, expected_head_sha))
        self.state.log.append(f"update-branch #{number}")
        return self.state.branch_update

    def list_issue_comments(self, owner: str, repo: str, number: int) -> list[IssueComment]:
        self.calls.append(("list_issue_comments", (owner, repo, number)))
        return list(self.state.comments.get(number, []))

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        self.calls.append(("create_issue_comment", (owner, repo, number, body)))
        self.state.created_comments.append((number, body))
        self.state.log.append(f"comment #{number}")
        comment_id = self.state.next_comment_id
        self.state.next_comment_id += 1
        url = f"https://github.com/{owner}/{repo}/issues/{number}#issuecomment-{comment_id}"
        comment = IssueComment(comment_id, BOT, "Bot", body, url)
        self.state.comments.setdefault(number, []).append(comment)
        return url

    def update_issue_comment(self, owner: str, repo: str, comment_id: int, body: str) -> str:
        self.calls.append(("update_issue_comment", (owner, repo, comment_id, body)))
        self.state.updated_comments.append((comment_id, body))
        return f"https://github.com/{owner}/{repo}/issues/comments/{comment_id}"

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("graphql", (query, dict(variables or {}))))
        return {}

    def close(self) -> None:
        self.closed = True


class FakeApiFactory:
    """Builds FakeGitHubApi instances bound to a token and remembers every one."""

    def __init__(self, files: Mapping[str, str], state: FakeRepository | None = None) -> None:
        self.files = files
        self.state = state if state is not None else FakeRepository()
        self.created: list[FakeGitHubApi] = []

    def __call__(self, token: str) -> FakeGitHubApi:
        api = FakeGitHubApi(token=token, files=self.files, state=self.state)
        self.created.append(api)
        return api
