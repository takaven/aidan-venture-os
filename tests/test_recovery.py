"""Crash/restart recovery per safety mode."""
from __future__ import annotations

from aidan_core import execution, proof, recovery

from conftest import setup_action


def _claim(conn, slug, mode, *, lease_seconds):
    _vid, aid = setup_action(conn, slug=slug, autonomy_level=1, amount=10)
    execution.authorize_and_claim(conn, aid, safety_mode=mode, lease_seconds=lease_seconds)
    return aid


def _claimed_count(conn, aid):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM execution_attempt WHERE action_request_id = %s AND status = 'CLAIMED'",
            (aid,),
        )
        return cur.fetchone()[0]


def test_idempotent_expired_lease_is_reclaimed(migrated):
    aid = _claim(migrated, "rec-idem", "IDEMPOTENT", lease_seconds=-1)  # already expired
    out = recovery.recover_action(migrated, aid)
    assert out["outcome"] == "reclaimed" and out["attempt_number"] == 2
    assert execution.get_status(migrated, aid) == "RUNNING"
    assert _claimed_count(migrated, aid) == 1  # the fresh attempt


def test_reconcilable_uses_reconciler(migrated):
    aid = _claim(migrated, "rec-recon", "RECONCILABLE", lease_seconds=-1)

    def reconciler(action_id):
        return {
            "external_result_id": "rext",
            "reported_outcome": "success",
            "raw_payload": {"token": proof.expected_token(action_id)},
        }

    out = recovery.recover_action(migrated, aid, reconciler=reconciler)
    assert out["outcome"] == "reconciled"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 1


def test_unsafe_ambiguous_is_not_auto_retried(migrated):
    aid = _claim(migrated, "rec-unsafe", "UNSAFE", lease_seconds=-1)
    out = recovery.recover_action(migrated, aid)
    assert out["outcome"] == "recovery_required"
    assert execution.get_status(migrated, aid) == "RECOVERY_REQUIRED"
    assert _claimed_count(migrated, aid) == 0  # no automatic new claim


def test_lease_still_valid_is_not_recovered(migrated):
    aid = _claim(migrated, "rec-active", "IDEMPOTENT", lease_seconds=300)
    out = recovery.recover_action(migrated, aid)
    assert out["outcome"] == "lease_active"
