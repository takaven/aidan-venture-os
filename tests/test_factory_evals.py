"""Gate 4 — Track A machine-execution development eval matrix.

Consolidated end-to-end scenarios driving the REAL Factory runtime: governed
dispatch → durable result/artifacts → deterministic verification → proof-gated
completion, with bounded retry/timeout/recovery, completion-time governance,
capital discipline, provenance and result idempotency. Deterministic fixtures
prove machine-execution semantics only — never product quality, provider
intelligence, deployment, or commercial success. External worker dispatch is
at-least-once; canonical completion is exactly once.
"""
from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from aidan_core import approvals, budget, execution, killswitch, ventures
from aidan_core.errors import ApprovalRequiredError, ExecutionBlockedError, IdempotencyConflictError
from aidan_core.factory import runtime, spec as spec_mod

from conftest import setup_action
from factory_fakes import (
    ErrorWorker, FakeClock, FakeWorkerA, FlakyWorker, ScriptedWorker, SlowWorker,
    registry_with, spec_action,
)

_DONE = {"require": {"status": "done"}}


def _sc(migrated, slug, **kw):
    """A structured-contract spec_action expecting {"status": "done"}."""
    kw.setdefault("verifier_kind", "structured-contract")
    kw.setdefault("expected_output_contract", _DONE)
    return spec_action(migrated, slug, **kw)


def _verified(migrated, aid):
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'", (aid,))
        return cur.fetchone()[0]


def _attempts(migrated, aid):
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (aid,))
        return cur.fetchone()[0]


# ==========================================================================
# A–C: verification outranks worker self-report.
# ==========================================================================
def test_A_simple_success(migrated):
    vid, aid, sp = _sc(migrated, "e-A")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert _attempts(migrated, aid) == 1 and _verified(migrated, aid) == 1
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"  # lifecycle unchanged


def test_B_worker_success_verifier_rejects(migrated):
    vid, aid, sp = _sc(migrated, "e-B", max_attempts=1)
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "nope"})))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status != "SUCCEEDED" and _verified(migrated, aid) == 0
    assert execution.get_status(migrated, aid) == "FAILED"  # max_attempts=1 -> terminal


def test_C_worker_nonsuccess_verifier_passes(migrated):
    vid, aid, sp = _sc(migrated, "e-C")
    runtime.execute_action(migrated, aid, registry=registry_with(
        FakeWorkerA(reported_outcome="uncertain", structured_output={"status": "done"})))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True


# ==========================================================================
# D–H: retry / timeout / exhaustion.
# ==========================================================================
def test_D_worker_error_retry_success(migrated):
    vid, aid, sp = _sc(migrated, "e-D", max_attempts=2)
    w = FlakyWorker(fail_first=1, structured_output={"status": "done"})
    reg = registry_with(w)
    assert runtime.execute_action(migrated, aid, registry=reg).failure_class == "WORKER_ERROR"
    runtime.execute_action(migrated, aid, registry=reg)
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and _attempts(migrated, aid) == 2 and _verified(migrated, aid) == 1


def test_E_worker_error_exhaustion(migrated):
    vid, aid, sp = _sc(migrated, "e-E", max_attempts=1)
    r = runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert r.failure_class == "RETRY_EXHAUSTED" and r.action_status == "FAILED"
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert _verified(migrated, aid) == 0


def test_F_verification_reject_retry_success(migrated):
    vid, aid, sp = _sc(migrated, "e-F", max_attempts=2)
    reg = registry_with(ScriptedWorker([{"status": "nope"}, {"status": "done"}]))
    runtime.execute_action(migrated, aid, registry=reg)
    assert runtime.verify_and_complete(migrated, aid, actual_cost=10).verified is False
    runtime.execute_action(migrated, aid, registry=reg)
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'FAILED'", (aid,))
        assert cur.fetchone()[0] >= 1  # rejected proof preserved


def test_G_timeout_retry_success(migrated):
    vid, aid, sp = _sc(migrated, "e-G", timeout=5, max_attempts=2)
    clock = FakeClock()
    r1 = runtime.execute_action(migrated, aid, registry=registry_with(SlowWorker(clock, 10)), clock=clock)
    assert r1.failure_class == "TIMEOUT"
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})), clock=clock)
    assert runtime.verify_and_complete(migrated, aid, actual_cost=10).status == "SUCCEEDED"


def test_H_timeout_exhaustion(migrated):
    vid, aid, sp = _sc(migrated, "e-H", timeout=5, max_attempts=1)
    clock = FakeClock()
    r = runtime.execute_action(migrated, aid, registry=registry_with(SlowWorker(clock, 99)), clock=clock)
    assert r.failure_class == "RETRY_EXHAUSTED" and r.action_status == "FAILED" and _verified(migrated, aid) == 0


# ==========================================================================
# I–K: duplicate invocation, restart, atomicity.
# ==========================================================================
def test_I_duplicate_invocation_no_duplicate(migrated):
    vid, aid, sp = _sc(migrated, "e-I")
    reg = registry_with(FakeWorkerA(structured_output={"status": "done"}))
    runtime.execute_action(migrated, aid, registry=reg)
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=reg)  # RUNNING, not claimable
    runtime.verify_and_complete(migrated, aid, actual_cost=10)
    dup = runtime.verify_and_complete(migrated, aid, actual_cost=10)  # idempotent
    assert dup.duplicated is True and _attempts(migrated, aid) == 1 and _verified(migrated, aid) == 1


def test_J_durable_result_restart(migrated):
    vid, aid, sp = _sc(migrated, "e-J")
    w = FakeWorkerA(structured_output={"status": "done"})
    runtime.execute_action(migrated, aid, registry=registry_with(w))
    assert w.calls == 1
    res = runtime.resume_action(migrated, aid, actual_cost=10)  # fresh: no worker/registry
    assert res["status"] == "SUCCEEDED" and w.calls == 1


def test_K_proof_and_success_are_atomic(migrated):
    vid, aid, sp = _sc(migrated, "e-K")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    runtime.verify_and_complete(migrated, aid, actual_cost=10)
    # VERIFIED proof and SUCCEEDED status are recorded together (no lone state).
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'", (aid,))
        verified = cur.fetchone()[0]
    assert verified == 1 and execution.get_status(migrated, aid) == "SUCCEEDED"


# ==========================================================================
# L–T: governance before/after dispatch and completion; capital.
# ==========================================================================
def test_L_kill_before_first_dispatch(migrated):
    vid, aid, sp = _sc(migrated, "e-L")
    killswitch.engage_global(migrated, engaged_by="op")
    w = FakeWorkerA()
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(w))
    assert w.calls == 0


def test_M_kill_before_retry(migrated):
    vid, aid, sp = _sc(migrated, "e-M", max_attempts=3)
    w = FlakyWorker(fail_first=1)
    reg = registry_with(w)
    runtime.execute_action(migrated, aid, registry=reg)
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=reg)
    assert w.calls == 1


def test_N_policy_deny_before_retry(migrated):
    # A venture kill is a policy DENY (KILL_SWITCH_VENTURE) distinct from the global one.
    vid, aid, sp = _sc(migrated, "e-N", max_attempts=3)
    w = FlakyWorker(fail_first=1)
    reg = registry_with(w)
    runtime.execute_action(migrated, aid, registry=reg)
    killswitch.engage_venture(migrated, vid, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=reg)
    assert w.calls == 1 and _verified(migrated, aid) == 0


def test_O_require_approval_before_retry(migrated):
    vid, aid = setup_action(migrated, slug="e-O", autonomy_level=0, required_autonomy=2, amount=10, grant=1000)
    spec_mod.create_execution_spec(
        migrated, aid, worker_kind="fake-a", verifier_kind="structured-contract", timeout_seconds=60,
        max_attempts=3, capability_scope=["READ_REPOSITORY"], task_payload={"g": "x"}, expected_output_contract=_DONE)
    approvals.approve(migrated, runtime.request_dispatch_authorization(migrated, aid).approval_id, decided_by="board")
    w = FlakyWorker(fail_first=1, structured_output={"status": "done"})
    reg = registry_with(w)
    runtime.execute_action(migrated, aid, registry=reg)
    budget.grant_budget(migrated, vid, amount=500, currency="USD")  # inputs change -> approval stale
    with pytest.raises(ApprovalRequiredError):
        runtime.execute_action(migrated, aid, registry=reg)
    approvals.approve(migrated, runtime.request_dispatch_authorization(migrated, aid).approval_id, decided_by="board")
    assert runtime.execute_action(migrated, aid, registry=reg).dispatched is True


def test_P_exact_funded_retry_reuses_reservation(migrated):
    vid, aid, sp = _sc(migrated, "e-P", amount=10, grant=10, max_attempts=2)
    reg = registry_with(FlakyWorker(fail_first=1, structured_output={"status": "done"}))
    assert runtime.execute_action(migrated, aid, registry=reg).failure_class == "WORKER_ERROR"
    assert runtime.execute_action(migrated, aid, registry=reg).dispatched is True  # own reservation, not DENY
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM capital_entry WHERE action_request_id = %s AND entry_type = 'RESERVE'", (aid,))
        assert cur.fetchone()[0] == 1


def test_Q_exact_funded_completion_governance(migrated):
    vid, aid, sp = _sc(migrated, "e-Q", amount=10, grant=10)
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)  # recheck sees its own reservation
    assert out.status == "SUCCEEDED"
    with migrated.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account WHERE venture_id = %s", (vid,))
        assert cur.fetchone() == (Decimal("0.0000"), Decimal("10.0000"))  # reconciled once


def test_R_genuine_budget_denial(migrated):
    # A genuine shortage (another action consumed the venture's remaining budget) still DENYs.
    from aidan_core import actions

    vid, aid, sp = _sc(migrated, "e-R", amount=10, grant=20)
    o = actions.submit_action_request(migrated, venture_id=vid, action_type="spend", actor="a",
                                      idempotency_key="e-R-other", requested_amount=15).action_id
    budget.reserve_budget(migrated, o)  # reserves 15 of 20 -> 5 left
    w = FakeWorkerA()
    with pytest.raises(ExecutionBlockedError):  # this action needs 10, only 5 available (no own reservation yet)
        runtime.execute_action(migrated, aid, registry=registry_with(w))
    assert w.calls == 0


def test_S_kill_after_result_before_completion(migrated):
    vid, aid, sp = _sc(migrated, "e-S")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert _verified(migrated, aid) == 0 and execution.get_status(migrated, aid) != "SUCCEEDED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] >= 1  # result history preserved


def test_T_policy_deny_after_result_before_completion(migrated):
    vid, aid, sp = _sc(migrated, "e-T")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    killswitch.engage_venture(migrated, vid, engaged_by="op")  # venture-scoped policy DENY
    with pytest.raises(ExecutionBlockedError):
        runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert _verified(migrated, aid) == 0


# ==========================================================================
# U–X: recovery obeys spec / max_attempts / current authorization.
# ==========================================================================
def test_U_idempotent_recovery_actually_executes(migrated):
    vid, aid, sp = _sc(migrated, "e-U", max_attempts=2)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT", lease_seconds=-1)  # crash
    w = FakeWorkerA(structured_output={"status": "done"})
    res = runtime.resume_action(migrated, aid, registry=registry_with(w), actual_cost=10)
    assert res["outcome"] == "recovered_and_dispatched" and w.calls == 1
    assert runtime.verify_and_complete(migrated, aid, actual_cost=10).status == "SUCCEEDED"


def test_V_recovery_respects_max_attempts(migrated):
    vid, aid, sp = _sc(migrated, "e-V", max_attempts=1)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT", lease_seconds=-1)
    w = FakeWorkerA()
    res = runtime.resume_action(migrated, aid, registry=registry_with(w), actual_cost=10)
    assert res["status"] == "FAILED" and w.calls == 0 and _attempts(migrated, aid) == 1


def test_W_kill_during_ambiguous_recovery(migrated):
    vid, aid, sp = _sc(migrated, "e-W", max_attempts=3)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT", lease_seconds=-1)
    killswitch.engage_global(migrated, engaged_by="op")
    w = FakeWorkerA()
    with pytest.raises(ExecutionBlockedError):
        runtime.resume_action(migrated, aid, registry=registry_with(w), actual_cost=10)
    assert w.calls == 0


def test_X_unsafe_ambiguity_requires_recovery(migrated):
    vid, aid, sp = _sc(migrated, "e-X", max_attempts=3)
    execution.authorize_and_claim(migrated, aid, safety_mode="UNSAFE", lease_seconds=-1)
    res = runtime.resume_action(migrated, aid, registry=registry_with(FakeWorkerA()), actual_cost=10)
    assert res["outcome"] == "recovery_required" and execution.get_status(migrated, aid) == "RECOVERY_REQUIRED"


# ==========================================================================
# Y–AB: execution_result idempotency.
# ==========================================================================
def test_Y_result_exact_replay_converges(migrated):
    vid, aid = setup_action(migrated, slug="e-Y", autonomy_level=1, amount=10)
    r1, c1 = execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 1})
    r2, c2 = execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 1})
    assert c1 and not c2 and r1 == r2


def test_Z_result_changed_payload_conflicts(migrated):
    vid, aid = setup_action(migrated, slug="e-Z", autonomy_level=1, amount=10)
    execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 1})
    with pytest.raises(IdempotencyConflictError):
        execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 2})


def test_AA_result_changed_outcome_conflicts(migrated):
    vid, aid = setup_action(migrated, slug="e-AA", autonomy_level=1, amount=10)
    execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 1})
    with pytest.raises(IdempotencyConflictError):
        execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="fail", raw_payload={"a": 1})


def test_AB_result_cross_attempt_reuse_conflicts(migrated):
    vid, aid, sp = _sc(migrated, "e-AB", max_attempts=3)
    h1 = execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 1}, attempt_id=h1.attempt_id)
    execution.fail_attempt(migrated, aid, attempt_id=h1.attempt_id, failure_class="WORKER_ERROR", terminal=False)
    h2 = execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    with pytest.raises(IdempotencyConflictError):
        execution.record_execution_result(migrated, aid, external_result_id="x", reported_outcome="success", raw_payload={"a": 1}, attempt_id=h2.attempt_id)


# ==========================================================================
# AC–AF: late result, authority escalation, cross-venture, DB success guard.
# ==========================================================================
def test_AC_timed_out_attempt_has_no_result(migrated):
    vid, aid, sp = _sc(migrated, "e-AC", timeout=5, max_attempts=2)
    clock = FakeClock()
    runtime.execute_action(migrated, aid, registry=registry_with(SlowWorker(clock, 10, structured_output={"status": "done"})), clock=clock)
    # A timed-out attempt captures no result, so it can never later create success.
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 0
    assert _verified(migrated, aid) == 0


def test_AD_worker_authority_escalation_is_inert(migrated):
    vid, aid, sp = _sc(migrated, "e-AD")
    malicious = FakeWorkerA(structured_output={
        "status": "done", "set_status": "SUCCEEDED", "max_attempts": 999, "change_verifier_kind": "evil",
        "capability_scope": ["DEPLOY"], "grant_approval": True, "disable_kill": True})
    runtime.execute_action(migrated, aid, registry=registry_with(malicious))
    row = spec_mod.get_execution_spec(migrated, aid)
    assert row[3] == "fake-a" and row[7] == "structured-contract" and row[9] == 3  # spec unchanged
    assert list(row[10]) == ["READ_REPOSITORY"]
    # The injected keys are inert; only the real contract decides. {status: done} still verifies.
    assert runtime.verify_and_complete(migrated, aid, actual_cost=10).status == "SUCCEEDED"


def test_AE_cross_venture_spec_rejected(migrated):
    vidA, aid, sp = _sc(migrated, "e-AE")
    vidB = ventures.create_venture(migrated, slug="e-AE-b")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO execution_spec (action_request_id, venture_id, worker_kind, task_hash, "
                "expected_output_contract, verifier_kind, timeout_seconds, max_attempts, spec_hash) "
                "VALUES (%s, %s, 'w', 'h', '{}'::jsonb, 'v', 60, 3, 's')",
                (aid, vidB))


def test_AF_raw_sql_forced_succeeded_blocked(migrated):
    vid, aid, sp = _sc(migrated, "e-AF")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE action_request SET status = 'SUCCEEDED' WHERE id = %s", (aid,))
    # The canonical verified path still reaches SUCCEEDED.
    assert runtime.verify_and_complete(migrated, aid, actual_cost=10).status == "SUCCEEDED"
