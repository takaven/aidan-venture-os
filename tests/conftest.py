"""Shared test fixtures for the Gate 1 kernel.

Integration tests require a real PostgreSQL reachable via ``DATABASE_URL``.
When it is absent (e.g. local dev without Postgres), those tests skip cleanly;
pure-logic tests still run. PostgreSQL integration evidence is produced by the
Gate 1 CI workflow's ``postgres:16`` service.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# Make the in-repo package importable without an install (local dev).
sys.path.insert(0, str(REPO_ROOT / "packages" / "core"))


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


# Objects owned by migration 0001; dropped to guarantee a clean slate per test.
_DROP_SQL = """
DROP TABLE IF EXISTS audit_event CASCADE;
DROP TABLE IF EXISTS schema_migrations CASCADE;
DROP TYPE IF EXISTS lifecycle_state CASCADE;
DROP TYPE IF EXISTS run_status CASCADE;
DROP TYPE IF EXISTS investment_decision CASCADE;
DROP FUNCTION IF EXISTS audit_event_immutable() CASCADE;
"""


@pytest.fixture
def conn():
    """An autocommit connection to the canonical test database, or skip."""
    url = database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; PostgreSQL integration runs in CI")
    connection = __import__("psycopg").connect(url, autocommit=True)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def clean_db(conn):
    """Drop all Slice-1 objects so a test starts from an empty schema."""
    with conn.cursor() as cur:
        cur.execute(_DROP_SQL)
    return conn
