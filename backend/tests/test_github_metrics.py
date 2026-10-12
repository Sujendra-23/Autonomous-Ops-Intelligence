"""GitHub ingest tests: recorded fixtures behind httpx.MockTransport; no token, no network."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from app.ingest import github_metrics as gm

FIXTURES = Path(__file__).parent / "fixtures" / "github"
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def paged(request: httpx.Request, items: list, size: int, key: str | None = None) -> httpx.Response:
    """Serve ``items`` in pages of ``size`` with GitHub-style Link headers."""
    page = int(parse_qs(urlparse(str(request.url)).query).get("page", ["1"])[0])
    chunk = items[(page - 1) * size : page * size]
    headers = {}
    if page * size < len(items):
        query = dict(parse_qs(urlparse(str(request.url)).query, keep_blank_values=True))
        query = {k: v[0] for k, v in query.items()}
        query["page"] = str(page + 1)
        nxt = f"{request.url.scheme}://{request.url.host}{request.url.path}?{urlencode(query)}"
        headers["link"] = f'<{nxt}>; rel="next"'
    body = {"total_count": len(items), key: chunk} if key else chunk
    return httpx.Response(200, json=body, headers=headers)


def fake_github(page_size: int = 4):
    pulls = load("pulls.json")
    reviews = load("reviews.json")
    runs = load("workflow_runs.json")
    deployments = load("deployments.json")
    statuses = load("deployment_statuses.json")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        query = dict(request.url.params)
        if path.endswith("/pulls"):
            ordered = sorted(pulls, key=lambda p: p["updated_at"], reverse=True)
            return paged(request, ordered, page_size)
        if path.endswith("/reviews"):
            number = path.split("/")[-2]
            return paged(request, reviews[number], page_size)
        if "/actions/workflows/" in path:
            selected = [
                r
                for r in runs
                if r["head_branch"] == query["branch"] and r["status"] == query["status"]
            ]
            return paged(request, selected, page_size, key="workflow_runs")
        if path.endswith("/statuses"):
            return paged(request, statuses[path.split("/")[-2]], page_size)
        if path.endswith("/deployments"):
            assert query["environment"] == "production"
            return paged(request, deployments, 2)
        return httpx.Response(404, json={"message": "Not Found"})

    return gm.GitHubClient(transport=httpx.MockTransport(handler)), seen


def test_pull_requests_from_recorded_repo_across_pages():
    client, seen = fake_github()
    rows = gm.fetch_pull_requests(client, "o/r", LONG_AGO)
    by_number = {r.number: r for r in rows}
    assert sorted(by_number) == list(range(1, 10))  # 9 PRs over 3 pages of 4
    assert {n for n, r in by_number.items() if r.state == "merged"} == {1, 3, 5, 8}
    assert by_number[2].state == "closed" and by_number[2].merged_at is None
    assert {n for n, r in by_number.items() if r.state == "open"} == {4, 6, 7, 9}
    merged = by_number[1]
    assert merged.author_login == "Sujendra-23"
    assert merged.merged_at > merged.created_at
    assert merged.first_review_at is None  # solo repo: nobody reviewed
    assert sum(r.url.path.endswith("/pulls") for r in seen) == 3


def test_since_stops_paging_early():
    client, seen = fake_github(page_size=2)
    recent = max(p["updated_at"] for p in load("pulls.json"))
    rows = gm.fetch_pull_requests(client, "o/r", gm.parse_time(recent))
    assert [r.number for r in rows] == [9]
    assert sum(r.url.path.endswith("/pulls") for r in seen) == 1


def test_first_review_ignores_author_pending_and_unsubmitted():
    reviews = [
        {"user": {"login": "ada"}, "state": "COMMENTED", "submitted_at": "2026-01-05T09:30:00Z"},
        {"user": {"login": "bo"}, "state": "PENDING", "submitted_at": None},
        {"user": {"login": "cy"}, "state": "APPROVED", "submitted_at": "2026-01-05T12:00:00Z"},
        {"user": {"login": "bo"}, "state": "COMMENTED", "submitted_at": "2026-01-05T10:00:00Z"},
    ]
    assert gm.first_review_time(iter(reviews), "ada") == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert gm.first_review_time(iter(reviews[:1]), "ada") is None
    assert gm.first_review_time(iter([]), None) is None


def test_workflow_runs_become_deployments():
    client, _ = fake_github()
    rows = gm.fetch_workflow_runs(client, "o/r", "release-checks.yml", LONG_AGO)
    expected = [r for r in load("workflow_runs.json") if r["head_branch"] == "main"]
    assert len(rows) == len(expected) > 4
    assert {r.status for r in rows} == {"success", "failure"}
    assert sum(r.status == "failure" for r in rows) == sum(
        r["conclusion"] == "failure" for r in expected
    )
    assert all(r.environment == "production" and r.source == "actions" for r in rows)
    assert all(r.started_at <= r.finished_at for r in rows)


@pytest.mark.parametrize(
    "conclusion,status",
    [("success", "success"), ("timed_out", "failure"), ("cancelled", "other"), (None, "other")],
)
def test_run_conclusion_mapping(conclusion, status):
    def handler(request):
        run = {"id": 1, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:05:00Z"}
        return httpx.Response(200, json={"workflow_runs": [{**run, "conclusion": conclusion}]})

    client = gm.GitHubClient(transport=httpx.MockTransport(handler))
    (row,) = gm.fetch_workflow_runs(client, "o/r", "d.yml", LONG_AGO)
    assert row.status == status


def test_deployments_api_uses_earliest_terminal_status():
    client, _ = fake_github()
    rows = {r.external_id: r for r in gm.fetch_deployments(client, "o/r", LONG_AGO)}
    assert rows[903].status == "other" and rows[903].finished_at is None  # still running
    assert rows[902].status == "success"
    assert rows[902].finished_at == datetime(2026, 1, 8, 10, 6, tzinfo=UTC)
    assert rows[901].status == "failure"  # error counts; later 'inactive' is not an outcome
    assert rows[900].status == "other"


def test_deployments_api_since_filter():
    client, _ = fake_github()
    rows = gm.fetch_deployments(client, "o/r", datetime(2026, 1, 1, tzinfo=UTC))
    assert {r.external_id for r in rows} == {901, 902, 903}


def test_token_is_sent_only_when_set_and_never_in_errors():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(500, text="boom ghp_secret")

    anonymous = gm.GitHubClient(transport=httpx.MockTransport(handler))
    with pytest.raises(gm.GitHubError):
        list(anonymous.paginate("/x"))
    authed = gm.GitHubClient("ghp_secret", transport=httpx.MockTransport(handler))
    with pytest.raises(gm.GitHubError) as caught:
        list(authed.paginate("/x"))
    assert seen == [None, "Bearer ghp_secret"]
    assert "ghp_secret" not in str(caught.value)


def test_rate_limit_waits_then_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}
            )
        if calls["n"] == 2:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json=[{"ok": True}])

    sleeps: list[float] = []
    client = gm.GitHubClient(
        transport=httpx.MockTransport(handler), sleep=sleeps.append, now=lambda: 1000.0
    )
    assert list(client.paginate("/x")) == [{"ok": True}]
    assert sleeps == [31.0, 7.0]


def test_rate_limit_too_long_aborts_and_plain_403_does_not_sleep():
    sleeps: list[float] = []
    far = gm.GitHubClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "999999"}
            )
        ),
        sleep=sleeps.append,
        now=lambda: 0.0,
    )
    with pytest.raises(gm.GitHubError, match="not waiting"):
        list(far.paginate("/x"))
    forbidden = gm.GitHubClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(403)), sleep=sleeps.append
    )
    with pytest.raises(gm.GitHubError, match="HTTP 403"):
        list(forbidden.paginate("/x"))
    assert sleeps == []


def test_save_upserts_every_row_in_one_transaction():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    pr = gm.PullRequestRow("o/r", 1, "ada", "open", LONG_AGO, None, None, None, None)
    dep = gm.DeploymentRow("o/r", "actions", 5, "d", "production", "abc", "success", None, None)
    gm.save(connection, [pr, pr], [dep])
    first, second = cursor.executemany.call_args_list
    assert "ON CONFLICT (repo, number)" in first.args[0] and len(first.args[1]) == 2
    assert "ON CONFLICT (repo, source, external_id)" in second.args[0]
    assert second.args[1][0]["external_id"] == 5


def test_run_dry_run_writes_nothing_and_counts():
    client, _ = fake_github()
    args = gm.build_parser().parse_args(
        ["--repo", "o/r", "--since-days", "36500", "--workflow", "release-checks.yml", "--dry-run"]
    )
    connection = MagicMock()
    totals = gm.run(args, client, connection)
    assert totals["pull_requests"] == 9 and totals["deployments"] > 4
    connection.cursor.assert_not_called()


def test_actions_source_requires_workflow():
    client, _ = fake_github()
    args = argparse.Namespace(repo=["o/r"], since_days=1, source="actions", workflow=None)
    with pytest.raises(SystemExit):
        gm.run(args, client, None)


def test_sync_database_url():
    assert gm.sync_database_url("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
