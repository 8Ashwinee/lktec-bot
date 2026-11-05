from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from lktec_bot.adapters.github_http import GitHubHttpError, HttpGitHubApi

BASE = "https://api.github.test"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def api(sleeps: list[float]) -> HttpGitHubApi:
    return HttpGitHubApi("ghs_token", base_url=BASE, sleep=sleeps.append, max_attempts=3)


@respx.mock(base_url=BASE)
def test_get_file_content_decodes_base64(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    content = base64.b64encode(b"version: 1\n").decode()
    route = respx_mock.get("/repos/acme/widgets/contents/.github/lktec-bot.yml").respond(
        200, json={"encoding": "base64", "content": content}
    )

    text = api.get_file_content("acme", "widgets", ".github/lktec-bot.yml", ref="main")

    assert text == "version: 1\n"
    req = route.calls[0].request
    assert req.url.params["ref"] == "main"
    assert req.headers["authorization"] == "Bearer ghs_token"
    assert req.headers["user-agent"].startswith("lktec-bot/")
    assert req.headers["x-github-api-version"] == "2022-11-28"


@respx.mock(base_url=BASE)
def test_get_file_content_returns_none_for_404(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/contents/.github/lktec-bot.yml").respond(404)

    assert api.get_file_content("acme", "widgets", ".github/lktec-bot.yml") is None


@respx.mock(base_url=BASE)
def test_get_file_content_rejects_directories(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/contents/.github").respond(200, json=[{"name": "x"}])

    with pytest.raises(GitHubHttpError, match="not a file"):
        api.get_file_content("acme", "widgets", ".github")


@respx.mock(base_url=BASE)
def test_retries_5xx_then_succeeds(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post("/repos/acme/widgets/issues/7/comments")
    route.side_effect = [
        httpx.Response(502, text="bad gateway"),
        httpx.Response(201, json={"html_url": "https://github.test/c/1"}),
    ]

    url = api.create_issue_comment("acme", "widgets", 7, "hello")

    assert url == "https://github.test/c/1"
    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock(base_url=BASE)
def test_retries_secondary_rate_limit_with_retry_after(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post("/repos/acme/widgets/pulls/3/reviews")
    route.side_effect = [
        httpx.Response(
            403,
            headers={"retry-after": "7"},
            json={"message": "You have exceeded a secondary rate limit."},
        ),
        httpx.Response(200, json={"html_url": "https://github.test/r/1"}),
    ]

    url = api.create_review("acme", "widgets", 3, "APPROVE", "lgtm")

    assert url == "https://github.test/r/1"
    assert sleeps == [7.0]
    assert json.loads(route.calls[0].request.content) == {"event": "APPROVE", "body": "lgtm"}


@respx.mock(base_url=BASE)
def test_rate_limit_without_retry_after_uses_exhausted_header_or_body(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post("/repos/acme/widgets/issues/1/comments")
    route.side_effect = [
        httpx.Response(429, headers={"x-ratelimit-remaining": "0"}, text="slow down"),
        httpx.Response(403, text="You have exceeded a secondary rate limit, try later"),
        httpx.Response(201, json={"html_url": "u"}),
    ]

    assert api.create_issue_comment("acme", "widgets", 1, "x") == "u"
    assert sleeps == [1.0, 2.0]


@respx.mock(base_url=BASE)
def test_gives_up_after_max_attempts(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.put("/repos/acme/widgets/pulls/5/update-branch").respond(503, text="down")

    with pytest.raises(GitHubHttpError, match="503") as excinfo:
        api.update_branch("acme", "widgets", 5, "abc123")

    assert excinfo.value.status == 503
    assert route.call_count == 3
    assert len(sleeps) == 2


@respx.mock(base_url=BASE)
def test_non_retryable_errors_raise_immediately(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post("/repos/acme/widgets/issues/1/comments").respond(
        403, json={"message": "Resource not accessible by integration"}
    )

    with pytest.raises(GitHubHttpError, match="not accessible"):
        api.create_issue_comment("acme", "widgets", 1, "x")

    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_update_branch_returns_message(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.put("/repos/acme/widgets/pulls/5/update-branch").respond(
        202, json={"message": "Updating pull request branch.", "url": "u"}
    )

    assert api.update_branch("acme", "widgets", 5, "abc123") == "Updating pull request branch."
    assert json.loads(route.calls[0].request.content) == {"expected_head_sha": "abc123"}


@respx.mock(base_url=BASE)
def test_enable_auto_merge_uses_graphql(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("/graphql").respond(
        200,
        json={
            "data": {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"url": "https://github.test/acme/widgets/pull/9"}
                }
            }
        },
    )

    url = api.enable_auto_merge("PR_node", "SQUASH")

    assert url == "https://github.test/acme/widgets/pull/9"
    body = json.loads(route.calls[0].request.content)
    assert "enablePullRequestAutoMerge" in body["query"]
    assert body["variables"] == {"pullRequestId": "PR_node", "mergeMethod": "SQUASH"}


@respx.mock(base_url=BASE)
def test_graphql_errors_raise(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    respx_mock.post("/graphql").respond(
        200, json={"data": None, "errors": [{"message": "Pull request is not mergeable"}]}
    )

    with pytest.raises(GitHubHttpError, match="not mergeable"):
        api.graphql("mutation { x }")


@respx.mock(base_url=BASE)
def test_graphql_without_data_raises(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    respx_mock.post("/graphql").respond(200, json={})

    with pytest.raises(GitHubHttpError, match="without data"):
        api.graphql("query { x }")


@respx.mock(base_url=BASE)
def test_backoff_is_capped(respx_mock: respx.MockRouter) -> None:
    sleeps: list[float] = []
    api = HttpGitHubApi(
        "t", base_url=BASE, sleep=sleeps.append, max_attempts=2, max_backoff_seconds=3.0
    )
    route = respx_mock.post("/graphql")
    route.side_effect = [
        httpx.Response(503, headers={"retry-after": "120"}),
        httpx.Response(200, json={"data": {"ok": True}}),
    ]

    assert api.graphql("query { x }") == {"ok": True}
    assert sleeps == [3.0]


def test_default_client_when_none_is_given() -> None:
    api = HttpGitHubApi("t")

    assert isinstance(api._client, httpx.Client)
