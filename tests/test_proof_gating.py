"""Construction-level proof gating (bounded correction).

Proves: (1) no production API accepts a caller-supplied VERIFIED verdict;
(2) canonical SUCCEEDED requires a matching VERIFIED proof for the same action;
(3) duplicate completion never infers success from one signal alone.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import budget, db, execution, proof, ventures
from aidan_core.errors import ExecutionBlockedError, InconsistentCanonicalStateError

from conftest import setup_action


def _claimed(conn, slug, *, amount=10, grant=100):
    vid, aid = setup_action(conn, slug=slug, autonomy_level=1, amount=amount, grant=grant)
    execution.authorize_and_claim(conn, aid, safety_mode="IDEMPOTENT")
    return vid, aid


def _complete_ok(conn, aid, actual=10):
    return execution.complete_execution(
        conn, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": proof.expected_token(aid)}, actual_cost=actual,
    )


# --------------------------------------------------------------------------
# Proof creation boundary.
# --------------------------------------------------------------------------
def test_no_public_verdict_supplying_api():
    # The old caller-supplied-result helper is gone; the only writer derives it.
    assert not hasattr(proof, "insert_receipt")
    assert not hasattr(proof, "create_receipt")


def test_record_receipt_derives_verdict_and_cannot_be_forced(migrated):
    _vid, aid = _claimed(migrated, "pg-derive")
    with db.transaction(migrated) as cur:
        rid, _ = execution._upsert_result(cur, aid, None, "e1", "success", {"token": "wrong"})
        verdict, _pid = proof._record_receipt(cur, aid, rid, "success", {"token": "wrong"})
    assert verdict == "FAILED"  # bad evidence cannot yield VERIFIED, whatever the caller "wants"

    _vid2, aid2 = _claimed(migrated, "pg-derive-ok")
    with db.transaction(migrated) as cur:
        rid, _ = execution._upsert_result(cur, aid2, None, "e1", "success", {"token": proof.expected_token(aid2)})
        verdict, _pid = proof._record_receipt(cur, aid2, rid, "success", {"token": proof.expected_token(aid2)})
    assert verdict == "VERIFIED"


# --------------------------------------------------------------------------
# Success transition boundary.
# --------------------------------------------------------------------------
def test_generic_status_helper_cannot_reach_succeeded():
    assert ("RUNNING", "SUCCEEDED") not in execution._ALLOWED_STATUS


def test_naked_success_without_proof_fails(migrated):
    _vid, aid = _claimed(migrated, "pg-naked")
    with pytest.raises(ExecutionBlockedError):
        with db.transaction(migrated) as cur:
            execution._transition_to_success(cur, aid, actor="attacker")
    assert execution.get_status(migrated, aid) == "RUNNING"


def test_verified_proof_for_another_action_does_not_authorize(migrated):
    _va, a = _claimed(migrated, "pg-A")
    _vb, b = _claimed(migrated, "pg-B")
    _complete_ok(migrated, b)  # b has a VERIFIED proof
    with pytest.raises(ExecutionBlockedError):
        with db.transaction(migrated) as cur:
            execution._transition_to_success(cur, a, actor="attacker")  # a has no proof
    assert execution.get_status(migrated, a) == "RUNNING"


def test_failed_proof_does_not_authorize(migrated):
    _vid, aid = _claimed(migrated, "pg-failed")
    with migrated.cursor() as cur:
        cur.execute(
            "INSERT INTO proof_receipt (action_request_id, verification_type, verifier, result, evidence_hash) "
            "VALUES (%s, 't', 'v', 'FAILED', 'h')",
            (aid,),
        )
    with pytest.raises(ExecutionBlockedError):
        with db.transaction(migrated) as cur:
            execution._transition_to_success(cur, aid, actor="x")


def test_matching_verified_permits_success(migrated):
    _vid, aid = _claimed(migrated, "pg-ok")
    out = _complete_ok(migrated, aid)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert execution.get_status(migrated, aid) == "SUCCEEDED"


# --------------------------------------------------------------------------
# Duplicate completion consistency.
# --------------------------------------------------------------------------
def test_succeeded_plus_verified_is_safe_duplicate(migrated):
    _vid, aid = _claimed(migrated, "pg-dup", amount=40)
    _complete_ok(migrated, aid, actual=40)
    dup = _complete_ok(migrated, aid, actual=40)
    assert dup.duplicated is True and dup.status == "SUCCEEDED"


def test_verified_without_succeeded_is_inconsistent(migrated):
    _vid, aid = _claimed(migrated, "pg-inc1")
    # Forge a VERIFIED receipt (test-only) while status is still RUNNING.
    with migrated.cursor() as cur:
        cur.execute(
            "INSERT INTO proof_receipt (action_request_id, verification_type, verifier, result, evidence_hash) "
            "VALUES (%s, 't', 'v', 'VERIFIED', 'h')",
            (aid,),
        )
    with pytest.raises(InconsistentCanonicalStateError):
        _complete_ok(migrated, aid)


def test_succeeded_without_verified_is_inconsistent(migrated):
    _vid, aid = _claimed(migrated, "pg-inc2")
    # Force SUCCEEDED without a proof (test-only corruption).
    with migrated.cursor() as cur:
        cur.execute("UPDATE action_request SET status = 'SUCCEEDED' WHERE id = %s", (aid,))
    with pytest.raises(InconsistentCanonicalStateError):
        _complete_ok(migrated, aid)
