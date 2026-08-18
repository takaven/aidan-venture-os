"""Migration runner: discovery/checksum (pure) + bootstrap/rerun/drift (DB)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aidan_core import migrate
from aidan_core.errors import MigrationChecksumError, MigrationError

from conftest import MIGRATIONS_DIR


# --------------------------------------------------------------------------
# Pure (no database) — runs everywhere including local dev without Postgres.
# --------------------------------------------------------------------------
def test_discover_is_deterministic_and_checksummed():
    first = migrate.discover(MIGRATIONS_DIR)
    second = migrate.discover(MIGRATIONS_DIR)
    assert first == second, "discovery must be deterministic"
    assert first, "at least the 0001 foundation migration must be discovered"

    versions = [v for v, _name, _cs, _sql in first]
    assert versions == sorted(versions, key=int), "must be ordered by numeric prefix"
    assert versions[0] == "0001"

    for _version, _name, checksum, _sql in first:
        assert len(checksum) == 64 and all(c in "0123456789abcdef" for c in checksum)


# --------------------------------------------------------------------------
# Integration (PostgreSQL) — skipped locally without DATABASE_URL.
# --------------------------------------------------------------------------
def test_fresh_bootstrap_applies_all(clean_db):
    applied = migrate.apply(clean_db, MIGRATIONS_DIR)
    assert applied == ["0001"]

    with clean_db.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
        rows = cur.fetchall()
        assert [r[0] for r in rows] == ["0001"]

        cur.execute("SELECT to_regclass('public.audit_event') IS NOT NULL")
        assert cur.fetchone()[0] is True

        for enum_type in ("lifecycle_state", "run_status", "investment_decision"):
            cur.execute("SELECT 1 FROM pg_type WHERE typname = %s", (enum_type,))
            assert cur.fetchone() is not None, f"enum type {enum_type} must exist"


def test_rerun_is_a_noop(clean_db):
    assert migrate.apply(clean_db, MIGRATIONS_DIR) == ["0001"]
    # Second and third runs must apply nothing and not duplicate rows.
    assert migrate.apply(clean_db, MIGRATIONS_DIR) == []
    assert migrate.apply(clean_db, MIGRATIONS_DIR) == []
    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_migrations")
        assert cur.fetchone()[0] == 1


def test_checksum_drift_is_hard_failure(clean_db, tmp_path):
    # Copy migrations to a temp dir so we never mutate the committed file.
    work = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, work)
    assert migrate.apply(clean_db, work) == ["0001"]

    target = next(work.glob("0001_*.sql"))
    target.write_text(target.read_text(encoding="utf-8") + "\n-- tampered\n", encoding="utf-8")

    with pytest.raises(MigrationChecksumError):
        migrate.apply(clean_db, work)


def test_failed_migration_is_not_recorded(clean_db, tmp_path):
    work = tmp_path / "migrations"
    work.mkdir()
    (work / "0001_ok.sql").write_text("CREATE TABLE probe_ok (id int);", encoding="utf-8")
    (work / "0002_bad.sql").write_text("CREATE TABLE bad (", encoding="utf-8")  # invalid SQL

    with pytest.raises(MigrationError):
        migrate.apply(clean_db, work)

    with clean_db.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        recorded = [r[0] for r in cur.fetchall()]
        assert recorded == ["0001"], "the failed 0002 migration must not be recorded"
        cur.execute("SELECT to_regclass('public.bad') IS NULL")
        assert cur.fetchone()[0] is True, "the failed migration must leave no objects"
