"""Load pull requests and deployment runs from GitHub into the developer-metrics tables.

    GITHUB_TOKEN=... python -m app.ingest.github_metrics --repo owner/name \\
        --source actions --workflow deploy.yml --since-days 90

The token is read from the environment only (``GITHUB_TOKEN``, see ``Settings.github_token``);
public repositories also work without one at GitHub's low anonymous rate limit. Writes go to
the application database (``DATABASE_URL``), never to the restricted analytics logins.

What is collected (and what the DORA keys derived from it can and cannot claim) is documented
in docs/developer-metrics.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger("aoi.ingest.github")

API = "https://api.github.com"
PAGE_SIZE = 100
MAX_RATE_LIMIT_WAIT = 900.0  # seconds; longer waits abort instead of hanging a CI job


class GitHubError(RuntimeError):
    """A GitHub API call failed in a way retrying will not fix."""


@dataclass(frozen=True)
class PullRequestRow:
    repo: str
    number: int
    author_login: str | None
    state: str
    created_at: datetime
    first_review_at: datetime | None
    merged_at: datetime | None
    closed_at: datetime | None
    merge_commit_sha: str | None


@dataclass(frozen=True)
class DeploymentRow:
    repo: str
    source: str
    external_id: int
    name: str | None
    environment: str
    sha: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class GitHubClient:
    """Minimal paginated REST client with rate-limit handling."""

    def __init__(
        self,
        token: str = "",
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        base_url: str = API,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aoi-developer-metrics",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url, headers=headers, transport=transport, timeout=30.0
        )
        self._sleep = sleep
        self._now = now

    def close(self) -> None:
        self._http.close()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for _ in range(4):
            response = self._http.get(url, params=params)
            wait = self._rate_limit_wait(response)
            if wait is None:
                if response.status_code >= 400:
                    # Status only: response bodies can echo request details.
                    raise GitHubError(f"GitHub returned HTTP {response.status_code} for {url}")
                return response
            if wait > MAX_RATE_LIMIT_WAIT:
                raise GitHubError(f"GitHub rate limit; resets in {wait:.0f}s, not waiting")
            logger.warning("rate limited, sleeping %.0fs", wait)
            self._sleep(wait)
        raise GitHubError("GitHub rate limit persisted after retries")

    def _rate_limit_wait(self, response: httpx.Response) -> float | None:
        if response.status_code not in (403, 429):
            return None
        retry_after = response.headers.get("retry-after")
        if retry_after:
            return float(retry_after)
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = float(response.headers.get("x-ratelimit-reset", "0"))
            return max(reset - self._now(), 0.0) + 1.0
        return None  # a plain 403 (no access) is an error, not a rate limit

    def paginate(
        self, path: str, params: dict[str, Any] | None = None, *, key: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield items across pages, following the ``Link: rel="next"`` header."""
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": PAGE_SIZE, **(params or {})}
        while url:
            response = self._get(url, query)
            payload = response.json()
            yield from payload[key] if key else payload
            url = response.links.get("next", {}).get("url")
            query = None  # the next URL already carries the query string


def fetch_pull_requests(
    client: GitHubClient, repo: str, since: datetime, *, with_reviews: bool = True
) -> list[PullRequestRow]:
    rows: list[PullRequestRow] = []
    params = {"state": "all", "sort": "updated", "direction": "desc"}
    for pr in client.paginate(f"/repos/{repo}/pulls", params):
        if parse_time(pr["updated_at"]) < since:
            break  # sorted by updated desc: everything after is older
        author = (pr.get("user") or {}).get("login")
        created = parse_time(pr["created_at"])
        merged = parse_time(pr.get("merged_at"))
        closed = parse_time(pr.get("closed_at"))
        state = "merged" if merged else ("closed" if pr["state"] == "closed" else "open")
        first_review = None
        if with_reviews:
            reviews = client.paginate(f"/repos/{repo}/pulls/{pr['number']}/reviews")
            first_review = first_review_time(reviews, author)
        rows.append(
            PullRequestRow(
                repo,
                pr["number"],
                author,
                state,
                created,
                first_review,
                merged,
                closed,
                pr.get("merge_commit_sha"),
            )
        )
    return rows


def first_review_time(reviews: Iterator[dict[str, Any]], author: str | None) -> datetime | None:
    """Earliest submitted review by someone other than the author (pending drafts excluded)."""
    times = [
        parse_time(r["submitted_at"])
        for r in reviews
        if r.get("submitted_at")
        and r.get("state") != "PENDING"
        and (r.get("user") or {}).get("login") != author
    ]
    return min(times) if times else None


RUN_CONCLUSIONS = {
    "success": "success",
    "failure": "failure",
    "timed_out": "failure",
    "startup_failure": "failure",
}


def fetch_workflow_runs(
    client: GitHubClient,
    repo: str,
    workflow: str,
    since: datetime,
    *,
    branch: str = "main",
    environment: str = "production",
) -> list[DeploymentRow]:
    """Completed runs of one workflow on one branch, treated as deployments to ``environment``.

    Only point this at a workflow that actually deploys. Cancelled/skipped runs are stored as
    ``other`` and left out of rates.
    """
    params = {"branch": branch, "status": "completed", "created": f">={since:%Y-%m-%d}"}
    rows: list[DeploymentRow] = []
    path = f"/repos/{repo}/actions/workflows/{workflow}/runs"
    for run in client.paginate(path, params, key="workflow_runs"):
        rows.append(
            DeploymentRow(
                repo,
                "actions",
                run["id"],
                run.get("name"),
                environment,
                run.get("head_sha"),
                RUN_CONCLUSIONS.get(run.get("conclusion") or "", "other"),
                parse_time(run.get("run_started_at") or run.get("created_at")),
                parse_time(run.get("updated_at")),
            )
        )
    return rows


def fetch_deployments(
    client: GitHubClient, repo: str, since: datetime, *, environment: str = "production"
) -> list[DeploymentRow]:
    """GitHub Deployments API: outcome is the earliest terminal status of each deployment."""
    rows: list[DeploymentRow] = []
    for dep in client.paginate(f"/repos/{repo}/deployments", {"environment": environment}):
        created = parse_time(dep["created_at"])
        if created < since:
            break  # listed newest first
        terminal = [
            s
            for s in client.paginate(f"/repos/{repo}/deployments/{dep['id']}/statuses")
            if s.get("state") in ("success", "failure", "error")
        ]
        outcome = min(terminal, key=lambda s: s["created_at"]) if terminal else None
        status = "other"
        if outcome:
            status = "success" if outcome["state"] == "success" else "failure"
        rows.append(
            DeploymentRow(
                repo,
                "deployments",
                dep["id"],
                dep.get("task"),
                dep.get("environment") or environment,
                dep.get("sha"),
                status,
                created,
                parse_time(outcome["created_at"]) if outcome else None,
            )
        )
    return rows


UPSERT_PR = """
INSERT INTO public.pull_requests
    (repo, number, author_login, state, created_at, first_review_at, merged_at, closed_at,
     merge_commit_sha)
VALUES (%(repo)s, %(number)s, %(author_login)s, %(state)s, %(created_at)s, %(first_review_at)s,
        %(merged_at)s, %(closed_at)s, %(merge_commit_sha)s)
ON CONFLICT (repo, number) DO UPDATE SET
    author_login = EXCLUDED.author_login, state = EXCLUDED.state,
    created_at = EXCLUDED.created_at, first_review_at = EXCLUDED.first_review_at,
    merged_at = EXCLUDED.merged_at, closed_at = EXCLUDED.closed_at,
    merge_commit_sha = EXCLUDED.merge_commit_sha, ingested_at = now()
"""

UPSERT_DEPLOYMENT = """
INSERT INTO public.deployments
    (repo, source, external_id, name, environment, sha, status, started_at, finished_at)
VALUES (%(repo)s, %(source)s, %(external_id)s, %(name)s, %(environment)s, %(sha)s, %(status)s,
        %(started_at)s, %(finished_at)s)
ON CONFLICT (repo, source, external_id) DO UPDATE SET
    name = EXCLUDED.name, environment = EXCLUDED.environment, sha = EXCLUDED.sha,
    status = EXCLUDED.status, started_at = EXCLUDED.started_at,
    finished_at = EXCLUDED.finished_at, ingested_at = now()
"""


def save(
    connection: Any, pull_requests: list[PullRequestRow], deployments: list[DeploymentRow]
) -> None:
    """Idempotent upsert in one transaction (``connection`` is a DB-API connection)."""
    from dataclasses import asdict

    with connection, connection.cursor() as cursor:
        cursor.executemany(UPSERT_PR, [asdict(r) for r in pull_requests])
        cursor.executemany(UPSERT_DEPLOYMENT, [asdict(r) for r in deployments])


def sync_database_url(url: str) -> str:
    """The app uses asyncpg URLs; this script uses psycopg2."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", action="append", required=True, help="owner/name; repeatable")
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--source", choices=["actions", "deployments", "none"], default="actions")
    parser.add_argument("--workflow", help="workflow file or id that deploys (source=actions)")
    parser.add_argument("--branch", default="main", help="branch the deploy workflow runs on")
    parser.add_argument("--environment", default="production")
    parser.add_argument("--no-reviews", action="store_true", help="skip per-PR review lookups")
    parser.add_argument("--dry-run", action="store_true", help="fetch and count, write nothing")
    return parser


def run(args: argparse.Namespace, client: GitHubClient, connection: Any | None) -> dict[str, int]:
    if args.source == "actions" and not args.workflow:
        raise SystemExit("--workflow is required for --source actions (or use --source none)")
    since = datetime.now(UTC) - timedelta(days=args.since_days)
    totals = {"pull_requests": 0, "deployments": 0}
    for repo in args.repo:
        prs = fetch_pull_requests(client, repo, since, with_reviews=not args.no_reviews)
        deployments: list[DeploymentRow] = []
        if args.source == "actions":
            deployments = fetch_workflow_runs(
                client,
                repo,
                args.workflow,
                since,
                branch=args.branch,
                environment=args.environment,
            )
        elif args.source == "deployments":
            deployments = fetch_deployments(client, repo, since, environment=args.environment)
        if connection is not None and not args.dry_run:
            save(connection, prs, deployments)
        totals["pull_requests"] += len(prs)
        totals["deployments"] += len(deployments)
        logger.info("%s: %d pull requests, %d deployments", repo, len(prs), len(deployments))
    return totals


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    args = build_parser().parse_args(argv)
    settings = get_settings()
    token = settings.github_token.get_secret_value()
    if not token:
        logger.warning("GITHUB_TOKEN is not set: anonymous rate limit (60 requests/hour)")
    connection = None
    if not args.dry_run:
        import psycopg2

        connection = psycopg2.connect(sync_database_url(settings.database_url))
    client = GitHubClient(token)
    try:
        totals = run(args, client, connection)
    except GitHubError as exc:
        logger.error("ingest failed: %s", exc)
        return 1
    finally:
        client.close()
        if connection is not None:
            connection.close()
    print(f"pull_requests={totals['pull_requests']} deployments={totals['deployments']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
