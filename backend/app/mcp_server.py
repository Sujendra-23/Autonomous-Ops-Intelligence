"""MCP server over the analytics semantic layer (stdio transport).

It is a thin adapter: ``list_views`` / ``describe_view`` read ``CATALOG`` and ``run_query``
goes through the same ``validate_sql`` / ``execute_sql`` path as ``POST /api/intelligence/ask``,
so the closed SQL dialect, the single-view allowlist, the read-only restricted database role
and the row cap all apply unchanged. Nothing here talks to an LLM: the MCP client's model
writes the SQL from ``describe_view``.

Privacy split: by default every query is rewritten onto the ``analytics_masked`` schema and run
as ``aoi_analytics_masked``. Owner access needs ``MCP_ALLOW_OWNER_ACCESS=true`` *and* a
reachable ``INTELLIGENCE_OWNER_DATABASE_URL``; the flag alone grants nothing.

stdout carries the protocol, so nothing in this module may print to it.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.services.analytics import (
    CATALOG,
    MAX_ROWS,
    AnalyticsUnavailable,
    UnsafeQuery,
    execute_sql,
)

logger = logging.getLogger("aoi.mcp")

INSTRUCTIONS = (
    "Read-only analytics over curated Autonomous Operational Intelligence views. "
    "Call list_views, then describe_view for the columns, then run_query with ONE PostgreSQL "
    "SELECT over ONE view written as analytics.<view>. Supported: WHERE, GROUP BY, HAVING, "
    "ORDER BY, DISTINCT, LIMIT and COUNT/SUM/AVG/MIN/MAX. Not supported: joins, subqueries, "
    "CTEs, casts, arithmetic and other functions. ORDER BY must use original columns, not "
    "output aliases. Results are capped at "
    f"{MAX_ROWS} rows."
)

mcp = FastMCP("aoi-analytics", instructions=INSTRUCTIONS)


def owner_access_enabled() -> bool:
    """Owner (unmasked) access is opt-in per process via MCP_ALLOW_OWNER_ACCESS."""
    return get_settings().mcp_allow_owner_access


def _jsonable(value: Any) -> Any:
    """Make driver values (UUID, datetime, Decimal, ...) JSON-safe."""
    return json.loads(json.dumps(value, default=str))


@mcp.tool()
def list_views() -> dict[str, Any]:
    """List the queryable semantic views and their column counts."""
    return {
        "views": [
            {"name": f"analytics.{name}", "columns": len(columns)}
            for name, columns in CATALOG.items()
        ],
        "owners_masked": not owner_access_enabled(),
    }


@mcp.tool()
def describe_view(view: str) -> dict[str, Any]:
    """Describe one view's columns (name, type, meaning). Accepts `tasks` or `analytics.tasks`."""
    name = view.removeprefix("analytics.")
    if name not in CATALOG:
        raise ToolError(f"Unknown view. Available: {', '.join(f'analytics.{v}' for v in CATALOG)}")
    columns = [
        {"name": column, "description": description}
        for column, description in CATALOG[name].items()
    ]
    return {
        "view": f"analytics.{name}",
        "columns": columns,
        "owners_masked": not owner_access_enabled(),
    }


@mcp.tool()
async def run_query(sql: str) -> dict[str, Any]:
    """Run one validated, read-only SELECT over a single `analytics.<view>`.

    Returns `columns`, positional `rows`, `truncated` and `owners_masked`. Queries that fall
    outside the supported dialect are rejected with the reason.
    """
    read_owners = owner_access_enabled()
    try:
        result = await execute_sql(sql, read_owners=read_owners)
    except UnsafeQuery as exc:
        raise ToolError(f"Query rejected: {exc}") from None
    except AnalyticsUnavailable as exc:
        raise ToolError(f"Analytics unavailable: {exc}") from None
    except (SQLAlchemyError, TimeoutError, OSError):
        # Never surface driver text: it can carry hostnames, role names or SQL.
        raise ToolError("Query could not be completed") from None
    return {**result, "rows": [[_jsonable(v) for v in row] for row in result["rows"]]}


@mcp.resource("aoi://analytics/catalog", mime_type="application/json")
def catalog_resource() -> str:
    """The full semantic catalog as JSON."""
    return json.dumps({f"analytics.{view}": columns for view, columns in CATALOG.items()})


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    mode = "OWNER (unmasked)" if owner_access_enabled() else "masked"
    logger.info("aoi-analytics MCP server starting on stdio, %s access", mode)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
