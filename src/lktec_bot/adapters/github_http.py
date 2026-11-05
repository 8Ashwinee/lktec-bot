"""httpx implementation of :class:`~lktec_bot.ports.github_api.GitHubApi` (REST + GraphQL)."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from lktec_bot import __version__
from lktec_bot.ports.github_api import MergeMethod, ReviewEvent

DEFAULT_BASE_URL = "https://api.github.com"
USER_AGENT = f"lktec-bot/{__version__}"
API_VERSION = "2022-11-28"

_ENABLE_AUTO_MERGE = """
mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}) {
    pullRequest { url }
  }
}
"""


class GitHubHttpError(RuntimeError):
    """A GitHub call failed after retries, or GraphQL returned errors."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HttpGitHubApi:
    """One client, one token.

    Retries 5xx responses and secondary rate limits (403/429 carrying ``Retry-After`` or an
    exhausted ``X-RateLimit-Remaining``) with a bounded back-off. ``sleep`` is injectable so the
    tests do not wait.
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
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._max_backoff = max_backoff_seconds

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

    def enable_auto_merge(self, pull_request_id: str, merge_method: MergeMethod) -> str:
        data = self.graphql(
            _ENABLE_AUTO_MERGE, {"pullRequestId": pull_request_id, "mergeMethod": merge_method}
        )
        return str(data["enablePullRequestAutoMerge"]["pullRequest"]["url"])

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
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(str(error.get("message", error)) for error in payload["errors"])
            raise GitHubHttpError(f"GraphQL errors: {messages}", status=response.status_code)
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
        last_error = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            response = self._client.request(
                method, url, headers=self._headers, json=json, params=params
            )
            if response.is_success or (allow_404 and response.status_code == 404):
                return response
            last_error = f"{method} {path} -> {response.status_code}: {_short_body(response)}"
            retryable = response.status_code >= 500 or _is_secondary_rate_limit(response)
            if not retryable or attempt == self._max_attempts:
                raise GitHubHttpError(last_error, status=response.status_code)
            self._sleep(self._backoff(response, attempt))
        raise GitHubHttpError(last_error)  # pragma: no cover - loop always returns or raises

    def _backoff(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self._max_backoff)
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
