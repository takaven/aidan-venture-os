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


# Objects owned by migrations 0001–0003; dropped for a clean slate per test.
_DROP_SQL = """
DROP TABLE IF EXISTS capital_entry CASCADE;
DROP TABLE IF EXISTS budget_account CASCADE;
DROP TABLE IF EXISTS policy_decision CASCADE;
DROP TABLE IF EXISTS kill_switch CASCADE;
DROP TABLE IF EXISTS investment_decision_record CASCADE;
DROP TABLE IF EXISTS action_request CASCADE;
DROP TABLE IF EXISTS venture_mandate_version CASCADE;
DROP TABLE IF EXISTS venture CASCADE;
DROP TABLE IF EXISTS audit_event CASCADE;
DROP TABLE IF EXISTS schema_migrations CASCADE;
DROP TYPE IF EXISTS lifecycle_state CASCADE;
DROP TYPE IF EXISTS run_status CASCADE;
DROP TYPE IF EXISTS investment_decision CASCADE;
DROP TYPE IF EXISTS policy_decision_kind CASCADE;
DROP FUNCTION IF EXISTS audit_event_immutable() CASCADE;
DROP FUNCTION IF EXISTS append_only_guard() CASCADE;
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
    """Drop all kernel objects so a test starts from an empty schema."""
    with conn.cursor() as cur:
        cur.execute(_DROP_SQL)
    return conn


@pytest.fixture
def migrated(clean_db):
    """A clean database with all migrations applied."""
    from aidan_core import migrate

    migrate.apply(clean_db, MIGRATIONS_DIR)
    return clean_db
