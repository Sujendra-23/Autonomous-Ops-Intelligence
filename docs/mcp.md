# MCP server for the analytics layer

An [MCP](https://modelcontextprotocol.io) server that exposes the curated analytics views
(`analytics.tasks`, `analytics.risks`, `analytics.blockers`, and any view later added to
`CATALOG`) to MCP clients over **stdio**. Source: `backend/app/mcp_server.py`, built on the
official Python SDK (`mcp==1.30.0`, `FastMCP`).

It reuses the existing security machinery rather than re-implementing it:

| Concern | Where it is enforced |
| --- | --- |
| One SELECT over one allowlisted view, no joins/CTEs/casts/functions | `validate_sql` (SQLGlot positive AST allowlist) |
| Masked vs owner schema | `validate_sql(read_owners=...)` rewrites `analytics.x` to `analytics_masked.x` or `analytics_owner.x` |
| Restricted login, read-only transaction, timeouts, role check | `execute_sql` |
| 200-row cap, truncation flag | `validate_sql` + `execute_sql` |

`run_query` calls `execute_sql`, which re-validates at the execution boundary, so a client
cannot reach the database with anything `/api/intelligence/ask` would refuse.

## Tools and resource

| Name | Purpose |
| --- | --- |
| `list_views` | Names and column counts of every queryable view, plus `owners_masked`. |
| `describe_view(view)` | Column names, types and meanings. Accepts `tasks` or `analytics.tasks`. |
| `run_query(sql)` | Validate and run one SELECT. Returns `columns`, positional `rows`, `truncated`, `owners_masked`. Rejections return the reason (for example `Query rejected: Unsupported SQL construct`) so the client can fix its SQL. |
| resource `aoi://analytics/catalog` | The whole catalog as JSON. |

No tool calls an LLM: the connected client's model writes the SQL from `describe_view`. Driver
errors are never returned (they can contain hostnames, role names or SQL); the client sees
`Query could not be completed`.

## Masked by default, owner by explicit flag

* Default: every query is rewritten onto `analytics_masked` and runs as `aoi_analytics_masked`
  through `INTELLIGENCE_DATABASE_URL`. Assigned task owners read as `[MASKED]`.
* `MCP_ALLOW_OWNER_ACCESS=true` switches the whole process to `analytics_owner` and
  `INTELLIGENCE_OWNER_DATABASE_URL`. The flag alone is not a grant: without that URL the
  server fails closed (`Analytics unavailable: Analytics database is not configured`), and
  `execute_sql` still refuses a connection that is not the dedicated `aoi_analytics_owner`
  role. The mode is per process (no per-call override), and is logged to stderr at start.

## Run it

Docker stack (rebuild once so the image has the `mcp` package):

```sh
make build && make up
make mcp          # speaks MCP on stdout/stdin; MCP clients spawn this
```

`make mcp` runs inside the backend container, which reads `.env`; the database host in
`INTELLIGENCE_DATABASE_URL` must therefore be reachable from the container (`postgres`, not
`localhost`).

Without Docker, from `backend/` with the dependencies installed and the analytics roles
provisioned ([docs/analytics.md](analytics.md)):

```sh
INTELLIGENCE_DATABASE_URL='postgresql://aoi_analytics_masked:...@localhost:5432/aoi' \
  python -m app.mcp_server
```

Claude Desktop / Claude Code style client entry:

```json
{
  "mcpServers": {
    "aoi-analytics": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/autonomous-ops-intelligence/backend",
      "env": { "INTELLIGENCE_DATABASE_URL": "postgresql://aoi_analytics_masked:...@host/aoi" }
    }
  }
}
```

stdout carries the protocol; logs go to stderr.

## Trust model and limits

* stdio means the client spawns the server as a local process. There is no network listener
  and no per-request authentication: whoever can launch the process and holds the database
  URL is the principal, exactly like holding `INTELLIGENCE_API_KEY`. Do not put the owner URL
  into a shared MCP config. Remote/multi-user hosting would need a transport with auth and is
  out of scope.
* Results are organization-wide (no tenant row-level security), as for `/ask`. The server is
  not offered in `AUTH_MODE=oidc` deployments by design; do not point it at a multi-tenant
  database.
* Query results go to the MCP client's model. Masking happens in the database views, so
  masked mode never exposes owner identity to that model.

## Tests

`backend/tests/test_mcp_server.py` (mocked database layer; real validation and rewriting; an
in-memory protocol round trip; a real stdio subprocess handshake) and
`backend/tests/test_mcp_postgres.py` (the tools against the real views and roles; opt-in via
`ANALYTICS_TEST_ADMIN_URL`, required in CI). The Postgres test substitutes the connection
layer (queries run on the test cursor under `SET LOCAL ROLE`) rather than opening real login
connections, so the login-role identity check in `execute_sql` is covered by the mocked tests
only.
