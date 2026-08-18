"""Gate 1 exit evidence: one deterministic end-to-end governed action loop,
plus the required negative cases. Uses a simulated deterministic executor.
"""
from __future__ import annotations

import os
from decimal import Decimal

import psycopg
import pytest

from aidan_core import (
    actions,
    approvals,
    budget,
    execution,
    killswitch,
    proof,
    recovery,
    ventures,
)
from aidan_core.errors import ApprovalRequiredError, ExecutionBlockedError

from conftest import setup_action


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def test_gate1_end_to_end(migrated):
    # migrated ensures a clean, fully-migrated schema; we use our own connections
    # so we can simulate process restarts by closing and reopening.

    # --- session 1: set up, submit, evaluate, require approval ---
    c = _connect()
    try:
        vid = ventures.create_venture(c, slug="e2e", autonomy_level=0)          # 1
        ventures.append_mandate_version(c, vid, content_hash="m1")               # 2
        budget.grant_budget(c, vid, amount=100, currency="USD")                  # 3
        aid = actions.submit_action_request(                                     # 4
            c, venture_id=vid, action_type="spend", actor="a",
            idempotency_key="k1", required_autonomy=2, requested_amount=40,
        ).action_id
        dup = actions.submit_action_request(                                     # 5
            c, venture_id=vid, action_type="spend", actor="a",
            idempotency_key="k1", required_autonomy=2, requested_amount=40,
        ).action_id
        assert dup == aid
        outcome = execution.request_execution(c, aid)                            # 6, 7, 8
        assert outcome.decision == "REQUIRE_APPROVAL"
        assert execution.get_status(c, aid) == "AWAITING_APPROVAL"
        approval_id = outcome.approval_id
    finally:
        c.close()                                                               # 9 restart

    # --- session 2: approve, authorize+claim, external op, "crash" ---
    c = _connect()
    try:
        assert approvals.approve(c, approval_id, decided_by="board") == "APPROVED"  # 10
        handle = execution.authorize_and_claim(c, aid, safety_mode="IDEMPOTENT")    # 11, 12
        assert execution.get_status(c, aid) == "RUNNING"
        assert budget.get_account(c, vid, "USD")[2] == Decimal("40.0000")
        ext = "ext-1"
        payload = {"token": proof.expected_token(aid)}
        execution.record_execution_result(                                          # 13, 14
            c, aid, external_result_id=ext, reported_outcome="success",
            raw_payload=payload, attempt_id=handle.attempt_id,
        )
    finally:
        c.close()                                                                   # crash/restart

    # --- session 3: recover, duplicate callback, verify, complete once ---
    c = _connect()
    try:
        rec = recovery.recover_action(c, aid)                                       # 15
        assert rec["outcome"] == "result_present"
        execution.record_execution_result(                                          # 16 duplicate
            c, aid, external_result_id=ext, reported_outcome="success", raw_payload=payload
        )
        with c.cursor() as cur:                                                      # 17 stored once
            cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
            assert cur.fetchone()[0] == 1

        first = execution.complete_execution(                                        # 18-21
            c, aid, external_result_id=ext, reported_outcome="success",
            raw_payload=payload, actual_cost=25,
        )
        assert first.status == "SUCCEEDED" and first.verified and not first.duplicated
        dup_complete = execution.complete_execution(
            c, aid, external_result_id=ext, reported_outcome="success",
            raw_payload=payload, actual_cost=25,
        )
        assert dup_complete.duplicated is True                                       # once

        with c.cursor() as cur:                                                      # 22 no dup success
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
        acct = budget.get_account(c, vid, "USD")                                     # 24 no double-spend
        assert acct[3] == Decimal("25.0000") and acct[2] == 0
    finally:
        c.close()

    # --- session 4: final state survives reconnect ---
    c = _connect()
    try:
        assert execution.get_status(c, aid) == "SUCCEEDED"                           # 23
    finally:
        c.close()


# --------------------------------------------------------------------------
# Required negative cases.
# --------------------------------------------------------------------------
def test_expired_approval_cannot_execute(migrated):
    vid, aid = setup_action(migrated, slug="neg-exp", autonomy_level=0, required_autonomy=2, amount=40)
    outcome = execution.request_execution(migrated, aid, approval_ttl_seconds=-1)
    approvals.approve(migrated, outcome.approval_id, decided_by="board")
    with pytest.raises(ApprovalRequiredError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")


def test_stale_approval_after_policy_input_change(migrated):
    vid, aid = setup_action(migrated, slug="neg-stale", autonomy_level=0, required_autonomy=2, amount=40, grant=100)
    outcome = execution.request_execution(migrated, aid)
    approvals.approve(migrated, outcome.approval_id, decided_by="board")
    # Changing available budget changes the policy inputs -> the approval is stale.
    budget.grant_budget(migrated, vid, amount=50, currency="USD")
    with pytest.raises(ApprovalRequiredError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")


def test_kill_switch_after_approval_blocks_execution(migrated):
    vid, aid = setup_action(migrated, slug="neg-kill", autonomy_level=0, required_autonomy=2, amount=40)
    outcome = execution.request_execution(migrated, aid)
    approvals.approve(migrated, outcome.approval_id, decided_by="board")
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")


def test_budget_exhaustion_between_approval_and_execution(migrated):
    vid, aid = setup_action(migrated, slug="neg-budget", autonomy_level=0, required_autonomy=2, amount=80, grant=100)
    outcome = execution.request_execution(migrated, aid)
    approvals.approve(migrated, outcome.approval_id, decided_by="board")
    # Exhaust available budget via another reservation on the same venture.
    other = actions.submit_action_request(
        migrated, venture_id=vid, action_type="spend", actor="a",
        idempotency_key="other", required_autonomy=0, requested_amount=80,
    ).action_id
    budget.reserve_budget(migrated, other)
    with pytest.raises(ExecutionBlockedError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")


def test_missing_proof_cannot_establish_success(migrated):
    vid, aid = setup_action(migrated, slug="neg-proof", autonomy_level=1, amount=10)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    out = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": "wrong"}, actual_cost=10,
    )
    assert out.status == "FAILED"
    assert execution.get_status(migrated, aid) == "FAILED"


def test_unsafe_ambiguous_not_auto_retried(migrated):
    vid, aid = setup_action(migrated, slug="neg-unsafe", autonomy_level=1, amount=10)
    execution.authorize_and_claim(migrated, aid, safety_mode="UNSAFE", lease_seconds=-1)
    rec = recovery.recover_action(migrated, aid)
    assert rec["outcome"] == "recovery_required"
    assert execution.get_status(migrated, aid) == "RECOVERY_REQUIRED"
