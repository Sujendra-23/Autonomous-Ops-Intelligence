# Developer metrics (DORA keys, flow signals)

Pull requests and deployment runs are ingested from GitHub into two tables and exposed through
three analytics views, so `POST /api/intelligence/ask` (and the MCP server, once merged) can
answer questions such as "what was our change failure rate last month?".

```
GitHub REST API ──► python -m app.ingest.github_metrics ──► public.pull_requests
                                                            public.deployments
                                                                   │ migration 0007
                         analytics_masked / analytics_owner ◄──────┘
                         pull_requests · deployments · dora_weekly
```

## Setup

1. `alembic upgrade head` (migration `0007_developer_metrics`; single head, follows
   `0006_merge_heads`).
2. Grant the views to the analytics roles: fresh installs get this from
   `backend/evals/provision_analytics.sql`; databases provisioned earlier run
   `backend/evals/grant_developer_metrics.sql` once as a DBA.
3. Ingest (token from the environment only; a fine-grained read-only token with
   *Pull requests: read* and *Actions: read* on the repositories is enough; no secret is
   committed or logged):

   ```sh
   export GITHUB_TOKEN=...        # or set it in .env
   make ingest-github ARGS="--repo owner/name --workflow deploy.yml --since-days 90"
   # or: cd backend && python -m app.ingest.github_metrics --repo owner/name --workflow deploy.yml
   ```

   | Option | Meaning |
   | --- | --- |
   | `--repo owner/name` | Repeatable. |
   | `--source actions` (default) | Completed runs of `--workflow` on `--branch` (default `main`) count as deployments. Point it only at a workflow that really deploys. |
   | `--source deployments` | Use the GitHub Deployments API for `--environment` instead (outcome = earliest terminal status of each deployment). |
   | `--source none` | Pull requests only. |
   | `--environment` | Label stored on deployments (default `production`); only `production` feeds the DORA keys. |
   | `--no-reviews` | Skip the per-PR review lookup (saves one API call per PR). |
   | `--dry-run` | Fetch and count; write nothing. |

   Re-running is safe: rows are upserted on `(repo, number)` / `(repo, source, external_id)`.
   Rate limits are honoured (waits up to 15 minutes, otherwise stops with a clear error).
   Anonymous access works for public repositories at GitHub's low rate limit.

The tables are organization-wide, like the other analytics views (no workspace/RLS), and hold no
PR titles or bodies. The only identity is the PR author's GitHub login, which is `[MASKED]` in
`analytics_masked` and visible only in `analytics_owner`. That login is personal data about your own engineers, held for the operator's
benefit rather than as SaaS customer content, so the tables carry no account or workspace key and
are classified as unrelated in the data-subject export/erasure coverage check
(`app/services/data_subject.py`). To remove a person, delete or null their `author_login` rows
in `public.pull_requests` directly.

## Views

| View | Grain | Key columns |
| --- | --- | --- |
| `analytics.pull_requests` | one row per PR | `state`, `author` (masked), `created_at`, `first_review_at`, `merged_at`, `hours_to_first_review`, `hours_to_merge`, `deployed_at`, `lead_time_hours` |
| `analytics.deployments` | one row per deployment run | `environment`, `status`, `finished_at`, `failed` (1/0), `restored_at`, `hours_to_restore` |
| `analytics.dora_weekly` | one row per repo and ISO week (Monday, UTC) | `deployment_count`, `failed_deployment_count`, `change_failure_rate`, `median_lead_time_hours`, `restored_incident_count`, `median_time_to_restore_hours` |

The query dialect has no joins, arithmetic, `CASE` or percentiles, so every metric is
precomputed as a column and each key reduces to one simple query.

## The four DORA keys: definitions and queries

| Key | Definition used here | Query |
| --- | --- | --- |
| Deployment frequency | Successful production deployments per week. | `SELECT week_start, deployment_count FROM analytics.dora_weekly ORDER BY week_start` |
| Lead time for changes | Hours from PR `created_at` to the finish of the first successful production deployment that **started** at or after the PR's `merged_at`. | `SELECT AVG(lead_time_hours) FROM analytics.pull_requests WHERE is_merged = TRUE` (mean), or `median_lead_time_hours` in `dora_weekly` (median, by week of deployment) |
| Change failure rate | Failed ÷ (successful + failed) production deployments. `cancelled`/`skipped` runs and non-production environments are excluded. | `SELECT AVG(failed) FROM analytics.deployments WHERE is_production = TRUE`; per week: `change_failure_rate` |
| Time to restore | For a failed production deployment that starts an incident (the previous finished deployment of that repo and environment did not fail), hours until the next successful deployment. Consecutive failures count as one incident. Unresolved incidents have no value yet. | `SELECT AVG(hours_to_restore) FROM analytics.deployments WHERE is_production = TRUE`; per week: `median_time_to_restore_hours` |

Averaging a ratio over weeks weights a quiet week like a busy one; to aggregate across weeks,
sum `failed_deployment_count` and `deployment_count` instead of averaging `change_failure_rate`.

## What is a proxy (read before quoting these numbers)

These are DORA-*style* measurements from GitHub data alone, not a certified DORA assessment.

* **Deployments are workflow runs (or GitHub Deployment records), not observed production
  releases.** A green run of the configured workflow is assumed to mean the change reached
  production. If the workflow does not really deploy, the numbers describe CI, not delivery.
* **Lead time starts at PR creation, not at first commit.** First-commit time is not collected,
  so lead time is understated by the time spent coding before the PR was opened. It also assumes
  a linear `main` where the first deployment started after the merge contains the change.
* **Change failure rate counts failed deployment runs.** It does not see changes that deployed
  "successfully" and then caused an incident or a rollback, and it counts pipeline failures that
  never reached users. GitHub has no incident data.
* **Time to restore is "time to next green deployment".** There is no incident or alert data, so
  it measures pipeline recovery, which is an upper-bound-ish stand-in for service restoration
  only when fixes ship through the same workflow.
* **Review latency** (`hours_to_first_review`) is the first submitted review by someone other
  than the author. Comment-only discussion outside formal reviews is not counted. A repository
  with no reviews, such as a solo project, has no values.
* **Not covered:** SPACE's satisfaction/well-being and communication dimensions (they need
  surveys), and activity counts per person (intentionally excluded, and authors are masked).
  Only flow/efficiency signals from PRs are provided.
* Times are UTC; weeks start Monday. `updated_at` of a completed run stands in for its finish
  time.

## Verification status

* Ingest: unit tests against fixtures recorded from this repository's own GitHub API
  (`backend/tests/fixtures/github/`, see its README; Deployments-API fixtures are synthetic).
* Views and DORA arithmetic: PostgreSQL tests with seeded data and hand-computed expectations,
  including consecutive failures, unresolved incidents, staging and cancelled runs, masking and
  grants (`tests/test_analytics_postgres.py`, `tests/test_github_metrics_postgres.py`).
* A live run against the real GitHub API was done once against this repository using
  `release-checks.yml` as a stand-in deploy workflow, since this project has no deploy pipeline.
  That demonstrates the plumbing, not meaningful DORA values.
