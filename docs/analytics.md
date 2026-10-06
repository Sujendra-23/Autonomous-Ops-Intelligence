# Natural-language analytics

`POST /api/intelligence/ask` accepts `{"question":"How many open tasks are overdue?"}`
and an `X-API-Key`. It returns a table: `columns`, `rows`, `truncated`, and
`owners_masked`. Rows are positional arrays so duplicate SQL output labels do not
silently overwrite values. Claude generates SQL from the question and the column
descriptions in `app/services/analytics.py`; database results are never sent to Claude.

## Setup

1. Install `backend/requirements.txt` and run `alembic upgrade head` from `backend`.
2. As a DBA, run `backend/evals/provision_analytics.sql` against the application
   database. This creates fresh, unprivileged `aoi_analytics_masked` and
   `aoi_analytics_owner` roles. It deliberately fails if either role already exists.
3. Through your usual secret provisioning, enable LOGIN and assign separate strong
   passwords to those roles. They must have no role memberships, base-table grants,
   database/schema creation privileges, or application-owner privileges. Audit PUBLIC
   privileges too: do not grant PUBLIC access to application tables or sensitive schemas.
4. Set `INTELLIGENCE_DATABASE_URL` to the masked login's PostgreSQL URL and optionally
   `INTELLIGENCE_OWNER_DATABASE_URL` to the owner login's URL. `postgresql://` and
   `postgresql+asyncpg://` URLs are supported. There is no application-DB fallback.
5. Set separate random `INTELLIGENCE_API_KEY` and optional `INTELLIGENCE_OWNER_API_KEY`.
   The latter grants `intelligence:read_owners`; possession is the caller's permission.
   Equal credentials are rejected. The existing ingestion credential grants no access.
   Keep privileged credentials server-side; a browser UI should use a trusted gateway
   that maps authenticated users to permissions. This repo has no user identity/RBAC system.
6. Configure the existing `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`.

Example (normal analytics key in an environment variable):

```sh
curl http://localhost:8000/api/intelligence/ask \
  -H "X-API-Key: $INTELLIGENCE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"question":"How many tasks have no owner?"}'
```

## Semantic contract and limits

The logical views are `analytics.tasks`, `analytics.risks`, and `analytics.blockers`.
Each has one row per entity. Project/task IDs support grouping; free-text titles,
project names, descriptions, quotes, metadata, and blocker parties are intentionally
excluded because they may contain identities. The task owner field can contain either
a name or email. Normal callers see `[MASKED]` for assigned owners, NULL otherwise;
`has_owner` preserves missing-owner analytics. Open tasks include open, in_progress,
and blocked; `is_overdue` uses the transaction's current time and excludes done/cancelled.

The server rewrites validated logical views to separate masked/owner schemas.
The masked database role cannot read the owner schema or underlying tables. Masking
happens before filtering/aggregation, so aliasing `owner AS email`, `MAX(owner)`, or
filtering by a real owner cannot recover identity. Grouping masked owners combines all
assigned owners. Authorized callers can filter/group actual names and emails.

SQLGlot parses exactly one SELECT. A positive AST allowlist permits simple filters,
aggregations (COUNT/SUM/AVG/MIN/MAX), sorting, DISTINCT, and LIMIT over one view.
Joins, CTEs, subqueries, unions, casts, arbitrary functions, system columns, locks,
and writes are rejected. Only regenerated, validated SQL reaches the database.
Requests cap questions at 2,000 characters, SQL at 12,000 characters, and results
at 200 rows (an extra row detects truncation). Execution checks the dedicated role,
uses a read-only transaction, pg_catalog-only search path, 5-second statement timeout,
and 1-second lock timeout. Database/model error details and generated SQL are not
returned. Unsupported questions get 422; missing configuration or DB failures 503;
model failures 502; invalid credentials 401. Results are organization-wide: there is
no tenant/row-level authorization in this initial endpoint. Existing API routes retain
their existing authentication behavior; this endpoint does not secure those routes.

## Evaluation and tests

From `backend`:

```sh
pytest tests/test_analytics.py tests/test_analytics_postgres.py -q
python -m evals.run_analytics
python -m evals.run_analytics --live
```

`evals/analytics.json` contains exactly 20 questions, reference SQL, expected answer
rows, and synthetic fixtures. The default eval validates and executes reference SQL
against an in-memory SQLite semantic fixture, checking aggregates, statuses, overdue
and missing-owner counts, grouping, and masked output. `--live` calls Claude for each
question, validates its SQL, executes it against the same fixture, and compares actual
answer rows. It incurs model usage and tests NL-to-SQL quality; passing reference SQL
alone does **not** establish model accuracy. Neither mode accesses production data.
`build_fixture.py` regenerates the checked-in fixture; simplified IDs and precomputed
overdue flags keep the eval deterministic.

The PostgreSQL test is opt-in: set `ANALYTICS_TEST_ADMIN_URL` to an empty disposable
database on an isolated cluster, using a superuser. It executes the actual migration
and provisioning SQL, verifies both roles, masking, catalog shape, forbidden base-table
access and write grants, then rolls all changes back. It refuses a nonempty public
schema. Unit tests separately verify endpoint permissions, malicious SQL rejection,
read-only/timeout setup, role checks, and result truncation.

Implementation references: [SQLGlot AST documentation](https://sqlglot.com/sqlglot.html)
and [PostgreSQL view privileges](https://www.postgresql.org/docs/16/sql-createview.html).
