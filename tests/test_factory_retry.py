"""Gate 4 Slice 3 — bounded retries, timeouts, reauthorization, crash recovery.

A retryable attempt failure is NOT action failure while attempts remain: the
attempt is classified and the action returns to a re-claimable state, and every
retry re-authorizes through the existing claim machinery. The action is terminal
FAILED only on non-retryable failure or exhaustion. The immutable spec is
unchanged across attempts, and canonical SUCCESS remains proof-gated and once.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import approvals, budget, execution, killswitch, recovery
from aidan_core.errors import ApprovalRequiredError, ExecutionBlockedError
from aidan_core.factory import runtime, spec as spec_mod

from conftest import setup_action
from factory_fakes import (
    ErrorWorker, FakeClock, FakeWorkerA, FlakyWorker, ScriptedWorker, SlowWorker,
    registry_with, spec_action,
)

_DONE = {"require": {"status": "done"}}


def _attempts(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (aid,))
        return cur.fetchone()[0]


def _proofs(conn, aid, result):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = %s", (aid, result))
        return cur.fetchone()[0]


def _failure_class(conn, aid):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT failure_class FROM execution_attempt WHERE action_request_id = %s "
            "AND status = 'FAILED' ORDER BY attempt_number DESC LIMIT 1", (aid,))
        row = cur.fetchone()
        return row[0] if row else None


# --------------------------------------------------------------------------
# Worker error retry / exhaustion.
# --------------------------------------------------------------------------
def test_worker_error_then_retry_succeeds(migrated):
    vid, aid, sp = spec_action(migrated, "r-A", verifier_kind="structured-contract",
                               max_attempts=3, expected_output_contract=_DONE)
    worker = FlakyWorker(fail_first=1, structured_output={"status": "done"})
    reg = registry_with(worker)
    r1 = runtime.execute_action(migrated, aid, registry=reg)
    assert r1.failure_class == "WORKER_ERROR" and r1.action_status == "PENDING"
    r2 = runtime.execute_action(migrated, aid, registry=reg)
    assert r2.dispatched and r2.failure_class is None
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert _attempts(migrated, aid) == 2 and _proofs(migrated, aid, "VERIFIED") == 1
    # immutable spec unchanged across attempts.
    assert spec_mod.get_execution_spec(migrated, aid)[11] == sp.spec_hash


def test_worker_error_exhaustion_is_terminal(migrated):
    vid, aid, sp = spec_action(migrated, "r-B", max_attempts=1)
    r1 = runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert r1.failure_class == "RETRY_EXHAUSTED" and r1.action_status == "FAILED"
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert _proofs(migrated, aid, "VERIFIED") == 0 and _attempts(migrated, aid) == 1


# --------------------------------------------------------------------------
# Verification rejection is retryable; history preserved.
# --------------------------------------------------------------------------
def test_verification_reject_then_retry_succeeds(migrated):
    vid, aid, sp = spec_action(migrated, "r-C", verifier_kind="structured-contract",
                               max_attempts=2, expected_output_contract=_DONE)
    worker = ScriptedWorker([{"status": "nope"}, {"status": "done"}])
    reg = registry_with(worker)
    runtime.execute_action(migrated, aid, registry=reg)
    out1 = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out1.verified is False and execution.get_status(migrated, aid) == "PENDING"
    runtime.execute_action(migrated, aid, registry=reg)
    out2 = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out2.status == "SUCCEEDED" and out2.verified is True
    # rejected receipt preserved AND exactly one VERIFIED.
    assert _proofs(migrated, aid, "FAILED") >= 1 and _proofs(migrated, aid, "VERIFIED") == 1


# --------------------------------------------------------------------------
# Timeout (deterministic clock) retry / exhaustion; timed-out attempt yields no result.
# --------------------------------------------------------------------------
def test_timeout_then_retry_succeeds(migrated):
    vid, aid, sp = spec_action(migrated, "r-D", verifier_kind="structured-contract",
                               timeout=5, max_attempts=2, expected_output_contract=_DONE)
    clock = FakeClock()
    r1 = runtime.execute_action(
        migrated, aid, registry=registry_with(SlowWorker(clock, 10, structured_output={"status": "done"})), clock=clock)
    assert r1.failure_class == "TIMEOUT" and r1.action_status == "PENDING"
    with migrated.cursor() as cur:  # timed-out attempt captured NO result -> cannot later succeed
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 0
    runtime.execute_action(
        migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})), clock=clock)
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True


def test_timeout_exhaustion_is_terminal(migrated):
    vid, aid, sp = spec_action(migrated, "r-E", timeout=5, max_attempts=1)
    clock = FakeClock()
    r1 = runtime.execute_action(migrated, aid, registry=registry_with(SlowWorker(clock, 99)), clock=clock)
    assert r1.failure_class == "RETRY_EXHAUSTED" and r1.action_status == "FAILED"
    assert _proofs(migrated, aid, "VERIFIED") == 0


# --------------------------------------------------------------------------
# Reauthorization before each retry (kill / budget / approval).
# --------------------------------------------------------------------------
def test_kill_switch_blocks_retry(migrated):
    vid, aid, sp = spec_action(migrated, "r-F", max_attempts=3)
    worker = FlakyWorker(fail_first=1)
    reg = registry_with(worker)
    r1 = runtime.execute_action(migrated, aid, registry=reg)
    assert r1.action_status == "PENDING"
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=reg)
    assert worker.calls == 1  # retry never dispatched the worker


def test_budget_denies_retry(migrated):
    vid, aid, sp = spec_action(migrated, "r-I", max_attempts=3, amount=10, grant=15)
    r1 = runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert r1.failure_class == "WORKER_ERROR" and r1.action_status == "PENDING"
    # The attempt-1 reservation is held (10 of 15) -> available 5 < 10 -> retry DENY.
    w2 = ErrorWorker()
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(w2))
    assert w2.calls == 0


def test_require_approval_on_retry(migrated):
    vid, aid = setup_action(migrated, slug="r-H", autonomy_level=0, required_autonomy=2, amount=10, grant=1000)
    spec_mod.create_execution_spec(
        migrated, aid, worker_kind="fake-a", verifier_kind="structured-contract", timeout_seconds=60,
        max_attempts=3, capability_scope=["READ_REPOSITORY"], task_payload={"goal": "x"},
        expected_output_contract=_DONE)
    approvals.approve(migrated, runtime.request_dispatch_authorization(migrated, aid).approval_id, decided_by="board")
    worker = FlakyWorker(fail_first=1, structured_output={"status": "done"})
    reg = registry_with(worker)
    assert runtime.execute_action(migrated, aid, registry=reg).failure_class == "WORKER_ERROR"

    # Change policy inputs -> the earlier approval no longer binds the current state.
    budget.grant_budget(migrated, vid, amount=500, currency="USD")
    with pytest.raises(ApprovalRequiredError):
        runtime.execute_action(migrated, aid, registry=reg)
    assert worker.calls == 1  # not re-dispatched

    approvals.approve(migrated, runtime.request_dispatch_authorization(migrated, aid).approval_id, decided_by="board")
    assert runtime.execute_action(migrated, aid, registry=reg).dispatched is True


# --------------------------------------------------------------------------
# Worker output cannot control retry/failure classification.
# --------------------------------------------------------------------------
def test_worker_forged_failure_class_is_inert(migrated):
    vid, aid, sp = spec_action(migrated, "r-Q", max_attempts=2)
    worker = ErrorWorker(structured_output={"failure_class": "NONE", "max_attempts": 999})
    runtime.execute_action(migrated, aid, registry=registry_with(worker))
    assert _failure_class(migrated, aid) == "WORKER_ERROR"  # kernel-determined, not the forged claim


# --------------------------------------------------------------------------
# Crash recovery: resume verification from durable state; safety-mode recovery.
# --------------------------------------------------------------------------
def test_resume_verifies_from_durable_state_without_worker(migrated):
    vid, aid, sp = spec_action(migrated, "r-M", verifier_kind="structured-contract", expected_output_contract=_DONE)
    worker = FakeWorkerA(structured_output={"status": "done"})
    runtime.execute_action(migrated, aid, registry=registry_with(worker))
    assert worker.calls == 1
    res = runtime.resume_action(migrated, aid, actual_cost=10)  # fresh: no worker, no registry
    assert res["outcome"] == "verified_from_durable_state" and res["status"] == "SUCCEEDED"
    assert worker.calls == 1 and execution.get_status(migrated, aid) == "SUCCEEDED"


def test_resume_reports_terminal_states(migrated):
    vid, aid, sp = spec_action(migrated, "r-term", max_attempts=1)
    runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))  # -> FAILED
    assert runtime.resume_action(migrated, aid, actual_cost=10)["outcome"] == "terminally_failed"


def test_crashed_claim_recovers_by_safety_mode(migrated):
    # IDEMPOTENT: claimed attempt, expired lease, no result -> existing recovery reclaims.
    vid, aid, sp = spec_action(migrated, "r-crashI", max_attempts=3)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT", lease_seconds=-1)
    assert recovery.recover_action(migrated, aid)["outcome"] == "reclaimed"
    # UNSAFE: ambiguous crash is not auto-rerun -> RECOVERY_REQUIRED.
    vid2, aid2, sp2 = spec_action(migrated, "r-crashU", max_attempts=3)
    execution.authorize_and_claim(migrated, aid2, safety_mode="UNSAFE", lease_seconds=-1)
    assert recovery.recover_action(migrated, aid2)["outcome"] == "recovery_required"
    assert execution.get_status(migrated, aid2) == "RECOVERY_REQUIRED"


# --------------------------------------------------------------------------
# Duplicate invocation while an attempt awaits verification.
# --------------------------------------------------------------------------
def test_duplicate_execute_no_duplicate_attempt(migrated):
    vid, aid, sp = spec_action(migrated, "r-O")
    reg = registry_with(FakeWorkerA())
    runtime.execute_action(migrated, aid, registry=reg)
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=reg)
    assert _attempts(migrated, aid) == 1


def test_raw_sql_success_guard_still_blocks_across_retries(migrated):
    vid, aid, sp = spec_action(migrated, "r-guard", max_attempts=3)
    runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))  # attempt 1 fails -> PENDING
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE action_request SET status = 'SUCCEEDED' WHERE id = %s", (aid,))
    assert execution.get_status(migrated, aid) != "SUCCEEDED"
