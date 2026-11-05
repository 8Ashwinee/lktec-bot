"""httpx implementation of :class:`~lktec_bot.ports.github_api.GitHubApi` (REST + GraphQL)."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from lktec_bot.adapters._http_common import DEFAULT_BASE_URL, github_headers
from lktec_bot.ports.github_api import MergeMethod, ReviewEvent

# Methods that are safe to repeat after a 5xx or a dropped connection. POST and PATCH are not:
# the first attempt may have succeeded before the answer was lost, and a second comment,
# review or mutation is not something a retry may create.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})

_PULL_REQUEST_ID = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { id } }
}
"""

_ENABLE_AUTO_MERGE = """
mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}) {
    pullRequest { url }
  }
}
"""


class GitHubHttpError(RuntimeError):
    """A GitHub call failed after retries, the transport failed, or GraphQL returned errors."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HttpGitHubApi:
    """One client, one token.

    Retry policy: 5xx answers are retried for idempotent methods only; a secondary rate limit
    (403/429 with ``Retry-After``, an exhausted ``X-RateLimit-Remaining`` or the words in the
    body) is retried for every method, because GitHub rejected the request before acting on
    it; a transport error (connection dropped, timeout) is retried once for idempotent methods.
    A ``Retry-After`` above ``max_backoff_seconds`` fails the call instead of being capped: the
    job would be waiting for a limit it cannot see. ``sleep`` is injectable so tests do not wait.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._base_url = base_url.rstrip("/")
        self._headers = github_headers(f"Bearer {token}")
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._max_backoff = max_backoff_seconds

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        self._client.close()

    # -- GitHubApi -------------------------------------------------------------------------

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str | None:
        params = {"ref": ref} if ref else None
        response = self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params=params, allow_404=True
        )
        if response.status_code == 404:
            return None
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("encoding") != "base64"
            or "content" not in payload
        ):
            raise GitHubHttpError(f"{path} is not a file with base64 content")
        return base64.b64decode(payload["content"]).decode("utf-8")

    def create_review(
        self, owner: str, repo: str, number: int, event: ReviewEvent, body: str = ""
    ) -> str:
        response = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            json={"event": event, "body": body},
        )
        return str(response.json()["html_url"])

    def enable_auto_merge(
        self, owner: str, repo: str, number: int, merge_method: MergeMethod
    ) -> str:
        lookup = self.graphql(_PULL_REQUEST_ID, {"owner": owner, "name": repo, "number": number})
        node_id = _node(lookup, "repository", "pullRequest", "id")
        data = self.graphql(
            _ENABLE_AUTO_MERGE, {"pullRequestId": node_id, "mergeMethod": merge_method}
        )
        return _node(data, "enablePullRequestAutoMerge", "pullRequest", "url")

    def update_branch(self, owner: str, repo: str, number: int, expected_head_sha: str) -> str:
        response = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{number}/update-branch",
            json={"expected_head_sha": expected_head_sha},
        )
        return str(response.json().get("message", ""))

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        response = self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body}
        )
        return str(response.json()["html_url"])

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self._request(
            "POST", "/graphql", json={"query": query, "variables": dict(variables or {})}
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubHttpError(
                "GraphQL response is not JSON", status=response.status_code
            ) from exc
        if not isinstance(payload, dict):
            raise GitHubHttpError(
                f"GraphQL response is {type(payload).__name__}, not an object",
                status=response.status_code,
            )
        errors = payload.get("errors")
        if errors:
            raise GitHubHttpError(
                f"GraphQL errors: {_error_messages(errors)}", status=response.status_code
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GitHubHttpError("GraphQL response without data", status=response.status_code)
        return data

    # -- transport -------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        transport_retried = False
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.request(
                    method, url, headers=self._headers, json=json, params=params
                )
            except httpx.TransportError as exc:
                retry = (
                    method in IDEMPOTENT_METHODS
                    and not transport_retried
                    and attempt < self._max_attempts
                )
                if not retry:
                    raise GitHubHttpError(
                        f"{method} {path} failed: {type(exc).__name__}: {exc}"
                    ) from exc
                transport_retried = True
                self._sleep(min(float(2 ** (attempt - 1)), self._max_backoff))
                continue
            if response.is_success or (allow_404 and response.status_code == 404):
                return response
            error = f"{method} {path} -> {response.status_code}: {_short_body(response)}"
            if not self._retryable(method, response) or attempt >= self._max_attempts:
                raise GitHubHttpError(error, status=response.status_code)
            self._sleep(self._backoff(response, attempt, error))

    @staticmethod
    def _retryable(method: str, response: httpx.Response) -> bool:
        if response.status_code >= 500:
            return method in IDEMPOTENT_METHODS
        return _is_secondary_rate_limit(response)

    def _backoff(self, response: httpx.Response, attempt: int, error: str) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            if float(retry_after) > self._max_backoff:
                raise GitHubHttpError(
                    f"{error}; Retry-After {retry_after} s exceeds the {self._max_backoff:g} s "
                    "limit, giving up",
                    status=response.status_code,
                )
            return float(retry_after)
        return min(float(2 ** (attempt - 1)), self._max_backoff)


def _is_secondary_rate_limit(response: httpx.Response) -> bool:
    if response.status_code not in (403, 429):
        return False
    if "retry-after" in response.headers:
        return True
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    return "secondary rate limit" in response.text.lower()


def _short_body(response: httpx.Response, limit: int = 200) -> str:
    text = response.text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


def _error_messages(errors: object) -> str:
    if not isinstance(errors, list):
        return str(errors)
    return "; ".join(
        str(error.get("message", error)) if isinstance(error, dict) else str(error)
        for error in errors
    )


def _node(data: Mapping[str, Any], *path: str) -> str:
    """Walk ``path`` through a GraphQL ``data`` object; a missing or null node is an error."""
    current: Any = data
    for depth, key in enumerate(path, start=1):
        if not isinstance(current, dict) or current.get(key) is None:
            where = ".".join(path[:depth])
            raise GitHubHttpError(f"GraphQL response has no {where} (missing or null)")
        current = current[key]
    return str(current)
