"""Canonical success is impossible without a VERIFIED proof, and is atomic."""
from __future__ import annotations

from decimal import Decimal

from aidan_core import budget, execution, proof

from conftest import setup_action


def _claimed(conn, slug, *, amount=10, grant=100):
    vid, aid = setup_action(conn, slug=slug, autonomy_level=1, amount=amount, grant=grant)
    execution.authorize_and_claim(conn, aid, safety_mode="IDEMPOTENT")
    return vid, aid


def test_no_public_set_status_api():
    # No way to force canonical success outside the guarded completion path.
    assert not hasattr(execution, "set_status")


def test_success_requires_verified_proof(migrated):
    _vid, aid = _claimed(migrated, "cs-1")
    # A failed verification never yields SUCCEEDED.
    bad = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": "nope"}, actual_cost=10,
    )
    assert bad.status == "FAILED"
    assert execution.get_status(migrated, aid) == "FAILED"


def test_success_is_atomic_and_once(migrated):
    vid, aid = _claimed(migrated, "cs-2", amount=40)
    payload = {"token": proof.expected_token(aid)}
    first = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload=payload, actual_cost=40,
    )
    assert first.status == "SUCCEEDED" and not first.duplicated
    dup = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload=payload, actual_cost=40,
    )
    assert dup.duplicated is True

    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_event WHERE action_id = %s AND event_type = 'execution.succeeded'",
            (aid,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'",
            (aid,),
        )
        assert cur.fetchone()[0] == 1
    # Budget committed exactly once.
    assert budget.get_account(migrated, vid, "USD")[3] == Decimal("40.0000")
