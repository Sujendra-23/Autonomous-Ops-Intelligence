"""Offline reference eval or opt-in Claude generation eval; no production DB access.

From backend: python -m evals.run_analytics [--live]
SQLite holds the synthetic semantic fixture, not production tables. PostgreSQL
privileges/masking are tested separately in test_analytics_postgres.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from app.services.analytics import CATALOG, generate_sql, validate_sql

DATA = json.loads(Path(__file__).with_name("analytics.json").read_text())


def fixture_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS analytics_masked")
    for table, columns in CATALOG.items():
        conn.execute(f"CREATE TABLE analytics_masked.{table} ({', '.join(columns)})")
        for original in DATA["fixtures"][table]:
            row = original.copy()
            if table == "tasks" and row[4] is not None:
                row[4] = "[MASKED]"
            conn.execute(
                f"INSERT INTO analytics_masked.{table} VALUES "  # noqa: S608 — static catalog
                f"({','.join('?' for _ in row)})",
                row,
            )
    return conn


async def run(live: bool = False) -> int:
    conn = fixture_connection()
    passed = 0
    try:
        for index, case in enumerate(DATA["cases"], 1):
            try:
                sql = await generate_sql(case["question"]) if live else case["sql"]
                rows = [list(row) for row in conn.execute(validate_sql(sql))]
                success = rows == case["expected"]
            except Exception:  # Report failures without leaking model content/credentials.
                success = False
            passed += success
            print(f"{index:02d} {'PASS' if success else 'FAIL'} {case['question']}")
    finally:
        conn.close()
    print(f"{passed}/{len(DATA['cases'])} passed ({'Claude' if live else 'reference SQL'})")
    return 0 if passed == len(DATA["cases"]) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Calls Claude; incurs API usage")
    raise SystemExit(asyncio.run(run(parser.parse_args().live)))
