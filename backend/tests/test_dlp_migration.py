"""Verify backfill of existing text across workspaces, including old embeddings."""

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2 import sql
from psycopg2.extras import Json


def test_dlp_migration_backfills_each_workspace_and_erases_old_embedding():
    admin_url = os.environ.get("SAAS_TEST_ADMIN_URL")
    if not admin_url:
        if os.environ.get("REQUIRE_DATABASE_TESTS") == "true":
            pytest.fail("SAAS_TEST_ADMIN_URL is required for migration tests")
        pytest.skip("Set SAAS_TEST_ADMIN_URL to an isolated disposable PostgreSQL server")
    database = "aoi_dlp_migration_" + uuid.uuid4().hex[:12]
    parsed = urlsplit(admin_url)
    target = urlunsplit(parsed._replace(path="/" + database))
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True

    def migrate(revision):
        result = subprocess.run(  # noqa: S603 - fixed interpreter/module; disposable database
            [sys.executable, "-m", "alembic", "upgrade", revision],
            env={
                **os.environ,
                "DATABASE_URL": target.replace("postgresql://", "postgresql+asyncpg://"),
                "AUTH_MODE": "development",
                "ENVIRONMENT": "development",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        migrate("0006_merge_heads")
        with psycopg2.connect(target) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id FROM workspaces")
            first = cursor.fetchone()[0]
            second = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO workspaces(id,name,created_at,updated_at) VALUES(%s,%s,now(),now())",
                (second, "Synthetic second workspace"),
            )
            for workspace in (first, second):
                transcript_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO transcripts(id,workspace_id,title,content,source,status,"
                    "participants,"
                    "calendar_context,created_at,updated_at) "
                    "VALUES(%s,%s,%s,%s,'upload','received',%s,%s,now(),now())",
                    (
                        transcript_id,
                        workspace,
                        "alice@example.com",
                        "Call 415-555-2671 today.",
                        Json(["alice@example.com"]),
                        Json({"organizer": "alice@example.com"}),
                    ),
                )
                cursor.execute(
                    "INSERT INTO transcript_chunks(id,workspace_id,transcript_id,index,content,"
                    "token_estimate,embedding,created_at,updated_at) "
                    "VALUES(%s,%s,%s,0,%s,5,%s,now(),now())",
                    (
                        str(uuid.uuid4()),
                        workspace,
                        transcript_id,
                        "alice@example.com",
                        "[" + ",".join(["0.01"] * 1536) + "]",
                    ),
                )
        migrate("head")
        with psycopg2.connect(target) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT title,content,participants,calendar_context,sensitivity_label "
                "FROM transcripts"
            )
            rows = cursor.fetchall()
            assert len(rows) == 2
            assert all(row[-1] == "confidential" for row in rows)
            assert "alice@example.com" not in str(rows)
            assert "415-555-2671" not in str(rows)
            cursor.execute("SELECT content,embedding FROM transcript_chunks")
            assert cursor.fetchall() == [("[REDACTED:EMAIL]", None)] * 2
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
            )
        admin.close()
