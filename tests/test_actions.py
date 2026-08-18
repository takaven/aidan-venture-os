"""ActionRequest intake: idempotency, conflict, concurrency, hashing."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from aidan_core import actions, ventures
from aidan_core.actions import canonical_payload_hash
from aidan_core.errors import IdempotencyConflictError


# --------------------------------------------------------------------------
# Pure (no database).
# --------------------------------------------------------------------------
def test_canonical_hash_ignores_key_order():
    assert canonical_payload_hash({"a": 1, "b": 2}) == canonical_payload_hash({"b": 2, "a": 1})
    assert canonical_payload_hash({"a": 1}) != canonical_payload_hash({"a": 2})
    assert canonical_payload_hash(None) == canonical_payload_hash({})


# --------------------------------------------------------------------------
# Integration (PostgreSQL).
# --------------------------------------------------------------------------
def _audit_created_count(conn, action_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_event "
            "WHERE action_id = %s AND event_type = 'action_request.created'",
            (action_id,),
        )
        return cur.fetchone()[0]


def test_first_intake_creates_one_row(migrated):
    vid = ventures.create_venture(migrated, slug="ar-1")
    res = actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a",
        idempotency_key="k1", payload={"x": 1},
    )
    assert res.created is True
    assert actions.get_action_request(migrated, res.action_id) is not None
    assert _audit_created_count(migrated, res.action_id) == 1


def test_exact_duplicate_returns_same_id_without_new_audit(migrated):
    vid = ventures.create_venture(migrated, slug="ar-2")
    first = actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a",
        idempotency_key="k1", payload={"x": 1, "y": 2},
    )
    # Same key, same payload but different key ordering -> still a duplicate.
    second = actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a",
        idempotency_key="k1", payload={"y": 2, "x": 1},
    )
    assert second.created is False
    assert second.action_id == first.action_id
    # Exactly one row and one creation audit event.
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 1
    assert _audit_created_count(migrated, first.action_id) == 1


def test_same_key_different_payload_conflicts(migrated):
    vid = ventures.create_venture(migrated, slug="ar-3")
    actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a",
        idempotency_key="k1", payload={"x": 1},
    )
    with pytest.raises(IdempotencyConflictError):
        actions.submit_action_request(
            migrated, venture_id=vid, action_type="probe", actor="a",
            idempotency_key="k1", payload={"x": 999},
        )
    # No second row was created.
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 1


def test_concurrent_duplicates_converge(migrated):
    vid = ventures.create_venture(migrated, slug="ar-concurrent")
    url = os.environ["DATABASE_URL"]

    def worker():
        conn = psycopg.connect(url, autocommit=True)
        try:
            return actions.submit_action_request(
                conn, venture_id=vid, action_type="probe", actor="a",
                idempotency_key="shared", payload={"x": 1},
            ).action_id
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: worker(), range(8)))

    # All callers converge on exactly one canonical ActionRequest.
    assert len(set(ids)) == 1
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 1
