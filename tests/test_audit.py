"""Audit append + DB-level immutability, and vocabulary separation."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import audit, migrate
from aidan_core.models import InvestmentDecision, LifecycleState, RunStatus

from conftest import MIGRATIONS_DIR


# --------------------------------------------------------------------------
# Pure (no database).
# --------------------------------------------------------------------------
def test_vocabularies_are_distinct_python_types():
    assert LifecycleState is not RunStatus
    assert RunStatus is not InvestmentDecision
    assert LifecycleState is not InvestmentDecision
    # The three concepts must not be one shared value space.
    lifecycle = {m.value for m in LifecycleState}
    runstatus = {m.value for m in RunStatus}
    decisions = {m.value for m in InvestmentDecision}
    assert lifecycle.isdisjoint(runstatus)
    assert lifecycle.isdisjoint(decisions)
    assert runstatus.isdisjoint(decisions)


# --------------------------------------------------------------------------
# Integration (PostgreSQL).
# --------------------------------------------------------------------------
def test_audit_append_and_read(clean_db):
    migrate.apply(clean_db, MIGRATIONS_DIR)
    with clean_db.cursor() as cur:
        event_id = audit.record_event(
            cur, event_type="unit.test", actor="tester", payload={"n": 1}
        )
        row = audit.get_event(cur, event_id)
    assert row[1] == "unit.test"
    assert row[2] == "tester"
    assert row[5] == {"n": 1}


def test_audit_update_is_rejected(clean_db):
    migrate.apply(clean_db, MIGRATIONS_DIR)
    with clean_db.cursor() as cur:
        event_id = audit.record_event(cur, event_type="e", actor="a")
    with pytest.raises(psycopg.errors.RaiseException):
        with clean_db.cursor() as cur:
            cur.execute("UPDATE audit_event SET actor = 'x' WHERE id = %s", (event_id,))


def test_audit_delete_is_rejected(clean_db):
    migrate.apply(clean_db, MIGRATIONS_DIR)
    with clean_db.cursor() as cur:
        event_id = audit.record_event(cur, event_type="e", actor="a")
    with pytest.raises(psycopg.errors.RaiseException):
        with clean_db.cursor() as cur:
            cur.execute("DELETE FROM audit_event WHERE id = %s", (event_id,))
    # The row must still be present.
    with clean_db.cursor() as cur:
        assert audit.get_event(cur, event_id) is not None


def test_db_vocabulary_types_are_separate(clean_db):
    migrate.apply(clean_db, MIGRATIONS_DIR)

    # Valid labels cast within their own type.
    valid = [
        ("BUILDING", "lifecycle_state"),
        ("FAILED", "run_status"),
        ("KILL", "investment_decision"),
    ]
    for label, typ in valid:
        with clean_db.cursor() as cur:
            cur.execute(f"SELECT %s::{typ}", (label,))
            assert cur.fetchone()[0] == label

    # A label from one type must be rejected by another type.
    invalid = [
        ("BUILDING", "run_status"),
        ("FAILED", "lifecycle_state"),
        ("KILL", "lifecycle_state"),
    ]
    for label, typ in invalid:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            with clean_db.cursor() as cur:
                cur.execute(f"SELECT %s::{typ}", (label,))
