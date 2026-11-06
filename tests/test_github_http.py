from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from lktec_bot.adapters.github_http import GitHubHttpError, HttpGitHubApi
from lktec_bot.ports.github_api import BranchUpdate, IssueComment, PullRequest, Review

BASE = "https://api.github.test"
CONTENTS = "/repos/acme/widgets/contents/.github/lktec-bot.yml"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def api(sleeps: list[float]) -> HttpGitHubApi:
    return HttpGitHubApi("ghs_token", base_url=BASE, sleep=sleeps.append, max_attempts=3)


def file_response(text: str = "version: 1\n") -> httpx.Response:
    content = base64.b64encode(text.encode()).decode()
    return httpx.Response(200, json={"encoding": "base64", "content": content})


@respx.mock(base_url=BASE)
def test_get_file_content_decodes_base64(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(CONTENTS).mock(return_value=file_response())

    text = api.get_file_content("acme", "widgets", ".github/lktec-bot.yml", ref="main")

    assert text == "version: 1\n"
    req = route.calls[0].request
    assert req.url.params["ref"] == "main"
    assert req.headers["authorization"] == "Bearer ghs_token"
    assert req.headers["user-agent"].startswith("lktec-bot/")
    assert req.headers["x-github-api-version"] == "2022-11-28"


@respx.mock(base_url=BASE)
def test_get_file_content_without_ref_reads_the_default_branch(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(CONTENTS).mock(return_value=file_response())

    api.get_file_content("acme", "widgets", ".github/lktec-bot.yml")

    assert "ref" not in route.calls[0].request.url.params


@respx.mock(base_url=BASE)
def test_get_file_content_returns_none_for_404(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(CONTENTS).respond(404)

    assert api.get_file_content("acme", "widgets", ".github/lktec-bot.yml") is None


@respx.mock(base_url=BASE)
def test_get_file_content_rejects_directories(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/contents/.github").respond(200, json=[{"name": "x"}])

    with pytest.raises(GitHubHttpError, match="not a file"):
        api.get_file_content("acme", "widgets", ".github")


# -- retries --------------------------------------------------------------------------------


@respx.mock(base_url=BASE)
def test_idempotent_request_retries_5xx_then_succeeds(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.get(CONTENTS)
    route.side_effect = [httpx.Response(502, text="bad gateway"), file_response()]

    assert api.get_file_content("acme", "widgets", ".github/lktec-bot.yml") == "version: 1\n"
    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock(base_url=BASE)
def test_post_is_not_retried_on_5xx(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post("/repos/acme/widgets/issues/7/comments").respond(502, text="bad")

    with pytest.raises(GitHubHttpError, match="502") as excinfo:
        api.create_issue_comment("acme", "widgets", 7, "hello")

    assert excinfo.value.status == 502
    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_post_retries_a_secondary_rate_limit_with_retry_after(
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
def test_other_client_errors_are_not_retried(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.put("/repos/acme/widgets/pulls/5/update-branch").respond(
        422, json={"message": "Validation Failed"}
    )

    with pytest.raises(GitHubHttpError, match="422") as excinfo:
        api.update_branch("acme", "widgets", 5, "abc123")

    assert excinfo.value.status == 422
    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_retry_after_beyond_the_limit_fails_instead_of_capping(
    respx_mock: respx.MockRouter,
) -> None:
    sleeps: list[float] = []
    api = HttpGitHubApi(
        "t", base_url=BASE, sleep=sleeps.append, max_attempts=3, max_backoff_seconds=30.0
    )
    route = respx_mock.get(CONTENTS).respond(429, headers={"retry-after": "120"}, text="wait")

    with pytest.raises(
        GitHubHttpError, match="Retry-After 120 s exceeds the 30 s limit"
    ) as excinfo:
        api.get_file_content("acme", "widgets", ".github/lktec-bot.yml")

    assert excinfo.value.status == 429
    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_exponential_backoff_is_capped(respx_mock: respx.MockRouter) -> None:
    sleeps: list[float] = []
    api = HttpGitHubApi(
        "t", base_url=BASE, sleep=sleeps.append, max_attempts=4, max_backoff_seconds=1.5
    )
    route = respx_mock.get(CONTENTS)
    route.side_effect = [httpx.Response(500), httpx.Response(500), file_response()]

    assert api.get_file_content("acme", "widgets", ".github/lktec-bot.yml") == "version: 1\n"
    assert sleeps == [1.0, 1.5]


@respx.mock(base_url=BASE)
def test_transport_error_is_retried_once_for_idempotent_methods(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.get(CONTENTS)
    route.side_effect = [httpx.ConnectError("connection reset"), file_response()]

    assert api.get_file_content("acme", "widgets", ".github/lktec-bot.yml") == "version: 1\n"
    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock(base_url=BASE)
def test_second_transport_error_is_a_github_http_error(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(CONTENTS)
    route.side_effect = [httpx.ConnectError("reset"), httpx.ReadTimeout("timed out")]

    with pytest.raises(GitHubHttpError, match=r"GET .* failed: ReadTimeout: timed out") as excinfo:
        api.get_file_content("acme", "widgets", ".github/lktec-bot.yml")

    assert excinfo.value.status is None
    assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)
    assert route.call_count == 2


@respx.mock(base_url=BASE)
def test_transport_error_is_not_retried_for_post(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post("/repos/acme/widgets/issues/1/comments")
    route.side_effect = httpx.ConnectError("reset")

    with pytest.raises(GitHubHttpError, match="ConnectError"):
        api.create_issue_comment("acme", "widgets", 1, "x")

    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_transport_error_is_not_retried_on_the_last_attempt(respx_mock: respx.MockRouter) -> None:
    sleeps: list[float] = []
    api = HttpGitHubApi("t", base_url=BASE, sleep=sleeps.append, max_attempts=1)
    route = respx_mock.get(CONTENTS)
    route.side_effect = httpx.ConnectError("reset")

    with pytest.raises(GitHubHttpError, match="ConnectError"):
        api.get_file_content("acme", "widgets", ".github/lktec-bot.yml")

    assert route.call_count == 1
    assert sleeps == []


# -- the other calls -----------------------------------------------------------------------


@respx.mock(base_url=BASE)
def test_update_branch_accepted(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.put("/repos/acme/widgets/pulls/5/update-branch").respond(
        202, json={"message": "Updating pull request branch.", "url": "u"}
    )

    assert api.update_branch("acme", "widgets", 5, "abc123") is BranchUpdate.ACCEPTED
    assert json.loads(route.calls[0].request.content) == {"expected_head_sha": "abc123"}


@respx.mock(base_url=BASE)
def test_update_branch_already_up_to_date_is_not_an_error(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.put("/repos/acme/widgets/pulls/5/update-branch").respond(
        422, json={"message": "There are no new commits on the base branch."}
    )

    assert api.update_branch("acme", "widgets", 5, "abc123") is BranchUpdate.UP_TO_DATE


@respx.mock(base_url=BASE)
def test_update_branch_other_422_is_an_error(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.put("/repos/acme/widgets/pulls/5/update-branch").respond(
        422, json={"message": "expected_head_sha does not match the current head"}
    )

    with pytest.raises(GitHubHttpError, match="does not match") as excinfo:
        api.update_branch("acme", "widgets", 5, "abc123")
    assert excinfo.value.status == 422


@respx.mock(base_url=BASE)
def test_get_pull_request_maps_the_fields(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/repos/acme/widgets/pulls/5").respond(
        200,
        json={
            "number": 5,
            "html_url": "https://github.test/acme/widgets/pull/5",
            "state": "open",
            "draft": False,
            "head": {"sha": "abc123"},
            "auto_merge": {"merge_method": "squash", "enabled_by": {"login": "x"}},
        },
    )

    pull_request = api.get_pull_request("acme", "widgets", 5)

    assert pull_request == PullRequest(
        number=5,
        html_url="https://github.test/acme/widgets/pull/5",
        state="open",
        draft=False,
        head_sha="abc123",
        auto_merge_method="SQUASH",
    )
    assert pull_request.open


@pytest.mark.parametrize(
    ("auto_merge", "expected"),
    [
        (None, None),
        ({"merge_method": "merge"}, "MERGE"),
        ({"merge_method": "rebase"}, "REBASE"),
        ({"merge_method": "fast-forward"}, None),
    ],
)
@respx.mock(base_url=BASE)
def test_get_pull_request_auto_merge_variants(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, auto_merge: object, expected: str | None
) -> None:
    respx_mock.get("/repos/acme/widgets/pulls/5").respond(
        200, json={"state": "closed", "draft": True, "head": {"sha": "s"}, "auto_merge": auto_merge}
    )

    pull_request = api.get_pull_request("acme", "widgets", 5)

    assert pull_request.auto_merge_method == expected
    assert pull_request.number == 5
    assert pull_request.draft is True
    assert not pull_request.open


@respx.mock(base_url=BASE)
def test_get_pull_request_without_a_head_is_an_error(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/pulls/5").respond(200, json={"number": 5})

    with pytest.raises(GitHubHttpError, match="no head"):
        api.get_pull_request("acme", "widgets", 5)


@respx.mock(base_url=BASE)
def test_list_reviews_maps_the_fields(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/repos/acme/widgets/pulls/5/reviews").respond(
        200,
        json=[
            {
                "user": {"login": "my-bot[bot]"},
                "state": "APPROVED",
                "commit_id": "abc",
                "html_url": "r1",
            },
            {"user": None, "state": "COMMENTED", "commit_id": "abc", "html_url": "r2"},
        ],
    )

    assert api.list_reviews("acme", "widgets", 5) == [
        Review("my-bot[bot]", "APPROVED", "abc", "r1"),
        Review("", "COMMENTED", "abc", "r2"),
    ]


@respx.mock(base_url=BASE)
def test_list_issue_comments_follows_the_pages(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    def entry(index: int) -> dict[str, object]:
        return {
            "id": index,
            "user": {"login": "my-bot[bot]", "type": "Bot"},
            "body": f"c{index}",
            "html_url": f"u{index}",
        }

    route = respx_mock.get("/repos/acme/widgets/issues/5/comments")
    route.side_effect = [
        httpx.Response(200, json=[entry(i) for i in range(100)]),
        httpx.Response(200, json=[entry(100), {"id": 101, "user": None, "body": None}, "junk"]),
    ]

    comments = api.list_issue_comments("acme", "widgets", 5)

    assert len(comments) == 102
    assert comments[0] == IssueComment(0, "my-bot[bot]", "Bot", "c0", "u0")
    assert comments[101] == IssueComment(101, "", "", "", "")
    assert route.calls[0].request.url.params["per_page"] == "100"
    assert route.calls[0].request.url.params["page"] == "1"
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock(base_url=BASE)
def test_list_that_is_not_a_list_is_an_error(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/issues/5/comments").respond(200, json={"message": "x"})

    with pytest.raises(GitHubHttpError, match="did not answer with a list"):
        api.list_issue_comments("acme", "widgets", 5)


@respx.mock(base_url=BASE)
def test_update_issue_comment_patches_the_body(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.patch("/repos/acme/widgets/issues/comments/42").respond(
        200, json={"html_url": "https://github.test/c/42"}
    )

    assert api.update_issue_comment("acme", "widgets", 42, "new") == "https://github.test/c/42"
    assert json.loads(route.calls[0].request.content) == {"body": "new"}


@respx.mock(base_url=BASE)
def test_patch_is_not_retried_on_5xx(
    api: HttpGitHubApi, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.patch("/repos/acme/widgets/issues/comments/42").respond(502)

    with pytest.raises(GitHubHttpError, match="502"):
        api.update_issue_comment("acme", "widgets", 42, "new")
    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_enable_auto_merge_resolves_the_node_id_then_mutates(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/graphql")
    route.side_effect = [
        httpx.Response(200, json={"data": {"repository": {"pullRequest": {"id": "PR_node"}}}}),
        httpx.Response(
            200,
            json={
                "data": {
                    "enablePullRequestAutoMerge": {
                        "pullRequest": {"url": "https://github.test/acme/widgets/pull/9"}
                    }
                }
            },
        ),
    ]

    url = api.enable_auto_merge("acme", "widgets", 9, "SQUASH")

    assert url == "https://github.test/acme/widgets/pull/9"
    lookup = json.loads(route.calls[0].request.content)
    assert "pullRequest(number: $number) { id }" in lookup["query"]
    assert lookup["variables"] == {"owner": "acme", "name": "widgets", "number": 9}
    mutation = json.loads(route.calls[1].request.content)
    assert "enablePullRequestAutoMerge" in mutation["query"]
    assert mutation["variables"] == {"pullRequestId": "PR_node", "mergeMethod": "SQUASH"}


@respx.mock(base_url=BASE)
def test_enable_auto_merge_reports_a_missing_pull_request(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("/graphql").respond(200, json={"data": {"repository": {"pullRequest": None}}})

    with pytest.raises(GitHubHttpError, match=r"no repository\.pullRequest \(missing or null\)"):
        api.enable_auto_merge("acme", "widgets", 9, "SQUASH")


@respx.mock(base_url=BASE)
def test_enable_auto_merge_reports_a_null_mutation_result(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/graphql")
    route.side_effect = [
        httpx.Response(200, json={"data": {"repository": {"pullRequest": {"id": "PR_node"}}}}),
        httpx.Response(200, json={"data": {"enablePullRequestAutoMerge": None}}),
    ]

    with pytest.raises(GitHubHttpError, match="no enablePullRequestAutoMerge"):
        api.enable_auto_merge("acme", "widgets", 9, "SQUASH")


@respx.mock(base_url=BASE)
def test_graphql_errors_raise(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    respx_mock.post("/graphql").respond(
        200, json={"data": None, "errors": [{"message": "Pull request is not mergeable"}, "odd"]}
    )

    with pytest.raises(GitHubHttpError, match="not mergeable; odd"):
        api.graphql("mutation { x }")


@respx.mock(base_url=BASE)
def test_graphql_errors_that_are_not_a_list_are_still_reported(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("/graphql").respond(200, json={"errors": "something broke"})

    with pytest.raises(GitHubHttpError, match="GraphQL errors: something broke"):
        api.graphql("query { x }")


@respx.mock(base_url=BASE)
def test_graphql_without_data_raises(api: HttpGitHubApi, respx_mock: respx.MockRouter) -> None:
    respx_mock.post("/graphql").respond(200, json={})

    with pytest.raises(GitHubHttpError, match="without data"):
        api.graphql("query { x }")


@respx.mock(base_url=BASE)
def test_graphql_tolerates_payloads_that_are_not_objects(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/graphql")
    route.side_effect = [httpx.Response(200, json=[1, 2]), httpx.Response(200, text="<html>")]

    with pytest.raises(GitHubHttpError, match="GraphQL response is list, not an object"):
        api.graphql("query { x }")
    with pytest.raises(GitHubHttpError, match="not JSON"):
        api.graphql("query { x }")


def test_default_client_when_none_is_given_and_close() -> None:
    api = HttpGitHubApi("t")

    assert isinstance(api._client, httpx.Client)
    assert api.base_url == "https://api.github.com"
    api.close()
    api.close()
    assert api._client.is_closed


@respx.mock(base_url=BASE)
def test_get_pull_request_reads_author_title_body_and_merge_state(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/pulls/7").respond(
        200,
        json={
            "number": 7,
            "html_url": "https://github.com/acme/widgets/pull/7",
            "state": "open",
            "draft": False,
            "title": "bump ruff from 0.1.0 to 0.1.1",
            "body": None,
            "mergeable_state": "behind",
            "user": {"login": "dependabot[bot]"},
            "head": {"sha": "abc"},
        },
    )

    pull_request = api.get_pull_request("acme", "widgets", 7)

    assert pull_request.title == "bump ruff from 0.1.0 to 0.1.1"
    assert pull_request.body == ""
    assert pull_request.author_login == "dependabot[bot]"
    assert pull_request.merge_state == "BEHIND"


@respx.mock(base_url=BASE)
def test_list_pull_requests_asks_for_open_ones_oldest_first(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    entry = {
        "number": 3,
        "state": "open",
        "head": {"sha": "abc"},
        "user": {"login": "someone"},
        "auto_merge": {"merge_method": "squash"},
    }
    route = respx_mock.get("/repos/acme/widgets/pulls").respond(200, json=[entry])

    pull_requests = api.list_pull_requests("acme", "widgets")

    params = route.calls[0].request.url.params
    assert (params["state"], params["sort"], params["direction"]) == ("open", "created", "asc")
    assert [pr.number for pr in pull_requests] == [3]
    assert pull_requests[0].auto_merge_method == "SQUASH"
    assert pull_requests[0].merge_state == ""


@respx.mock(base_url=BASE)
def test_list_pull_requests_rejects_an_entry_without_a_head(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/pulls").respond(200, json=[{"number": 3}])

    with pytest.raises(GitHubHttpError, match="a pull request in the list has no head"):
        api.list_pull_requests("acme", "widgets")


@respx.mock(base_url=BASE)
def test_list_pull_request_files_includes_the_old_name_of_a_rename(
    api: HttpGitHubApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/repos/acme/widgets/pulls/7/files").respond(
        200,
        json=[
            {"filename": "a.txt"},
            {"filename": "new.yml", "previous_filename": ".github/workflows/old.yml"},
        ],
    )

    paths = api.list_pull_request_files("acme", "widgets", 7)

    assert paths == ["a.txt", "new.yml", ".github/workflows/old.yml"]
