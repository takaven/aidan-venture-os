"""Deterministic proof: verification, immutability, no bypass."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import execution, proof

from conftest import setup_action


# --------------------------------------------------------------------------
# Pure verifier.
# --------------------------------------------------------------------------
def test_deterministic_verify():
    aid = "abc"
    ok = proof.deterministic_verify(aid, "success", {"token": proof.expected_token(aid)})
    assert ok[0] == "VERIFIED"
    assert proof.deterministic_verify(aid, "success", {"token": "wrong"})[0] == "FAILED"
    assert proof.deterministic_verify(aid, "failure", {"token": proof.expected_token(aid)})[0] == "FAILED"
    assert proof.deterministic_verify(aid, "success", None)[0] == "FAILED"


# --------------------------------------------------------------------------
# Integration.
# --------------------------------------------------------------------------
def _claimed(conn, slug, amount=10):
    _vid, aid = setup_action(conn, slug=slug, autonomy_level=1, amount=amount)
    execution.authorize_and_claim(conn, aid, safety_mode="IDEMPOTENT")
    return aid


def test_verified_completion_creates_receipt_and_success(migrated):
    aid = _claimed(migrated, "pf-1")
    payload = {"token": proof.expected_token(aid)}
    out = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload=payload, actual_cost=10,
    )
    assert out.status == "SUCCEEDED" and out.verified is True
    assert execution.get_status(migrated, aid) == "SUCCEEDED"


def test_failed_verification_does_not_succeed(migrated):
    aid = _claimed(migrated, "pf-2")
    out = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": "wrong"}, actual_cost=10,
    )
    assert out.status == "FAILED" and out.verified is False
    assert execution.get_status(migrated, aid) == "FAILED"
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'",
            (aid,),
        )
        assert cur.fetchone()[0] == 0


def test_worker_self_report_cannot_bypass_verifier(migrated):
    # Reported outcome "success" without the correct proof token is not proof.
    aid = _claimed(migrated, "pf-3")
    out = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"claimed": "done"}, actual_cost=10,
    )
    assert out.verified is False
    assert execution.get_status(migrated, aid) != "SUCCEEDED"


def test_proof_receipt_is_immutable(migrated):
    aid = _claimed(migrated, "pf-4")
    out = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": proof.expected_token(aid)}, actual_cost=10,
    )
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE proof_receipt SET result = 'FAILED' WHERE id = %s", (out.proof_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM proof_receipt WHERE id = %s", (out.proof_id,))
