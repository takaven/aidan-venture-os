"""Factory runtime — durable, claim-only worker dispatch + verification + recovery (Gate 4).

``execute_action`` drives an already-governed ActionRequest against its frozen,
immutable execution spec: it re-checks authorization *against that spec*, claims
a canonical execution attempt through the existing Gate 1 machinery, dispatches a
typed replaceable worker (with no DB access), and captures the worker's result as
a CLAIM into the existing execution_result table — with bounded retry (worker
error / timeout) that leaves the action re-claimable while attempts remain.
``verify_and_complete`` runs the immutable spec's deterministic verifier over
durable state and completes through the proof-gated path (re-checking current
governance first); ``resume_action`` recovers an interrupted action from durable
PostgreSQL state. Canonical SUCCESS remains proof-gated and once.

Authorization binding (load-bearing): a Policy decision or Approval created
before the spec was frozen cannot authorize dispatch. Dispatch performs a fresh
policy evaluation (inherently post-spec, since the spec must exist to dispatch)
and, when approval is required, demands a valid APPROVED approval that was
requested AFTER the spec existed. Pre-spec authorization is therefore ineligible;
fresh, spec-aware authorization is required. No parallel policy/approval engine
is introduced — the existing Gate 1 primitives are reused.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .. import audit, budget, db, execution, policy, proof
from ..errors import (
    AmbiguousExternalEffectError,
    ApprovalRequiredError,
    ExecutionBlockedError,
    NotFoundError,
    WorkerTimeoutError,
)
from . import artifacts as artifacts_mod
from . import spec as spec_mod
from . import test_execution
from . import verifiers as verifiers_mod
from .verifiers import VerificationRequest, VerifierRegistry, default_registry
from .workers import WorkerRegistry, WorkerRequest


@dataclass(frozen=True)
class RuntimeResult:
    action_request_id: str
    attempt_id: str
    worker_kind: str
    action_status: str          # post-dispatch canonical status
    dispatched: bool
    external_result_id: Optional[str] = None
    reported_outcome: Optional[str] = None
    failure_class: Optional[str] = None   # set when this attempt failed (WORKER_ERROR/TIMEOUT)


def _decide_failure(base_class: str, attempt_number: int, max_attempts: int):
    """Deterministic retry decision. Returns (terminal, final_failure_class)."""
    if base_class not in execution.RETRYABLE_FAILURE:
        return True, base_class
    if attempt_number >= max_attempts:
        return True, "RETRY_EXHAUSTED"
    return False, base_class


def _record_failure(conn, action_id, attempt_id, attempt_number, max_attempts, base_class, detail):
    """Classify + record an attempt failure. Returns (action_status, final_failure_class).

    ``final_failure_class`` is the canonical class actually persisted on the attempt
    (``RETRY_EXHAUSTED`` at exhaustion; otherwise the base class), so callers report
    the persisted classification rather than the raw underlying cause.
    """
    terminal, final_class = _decide_failure(base_class, attempt_number, max_attempts)
    to_state = execution.fail_attempt(
        conn, action_id, attempt_id=attempt_id, failure_class=final_class,
        detail={"base_class": base_class, **(detail or {})}, terminal=terminal,
    )
    return to_state, final_class


def _record_ambiguous_recovery_required(conn, action_id, attempt_id, detail):
    """Fail an attempt CLOSED after an unresolved ambiguous external effect: the attempt is FAILED
    (non-retryable), the action becomes RECOVERY_REQUIRED (not auto-claimable — see the execute_action
    guard), and the budget reservation is HELD (the spend may have occurred). No ordinary retry may
    dispatch again; only explicit recovery may reconcile the exact provider result later."""
    with db.transaction(conn) as cur:
        # failure_class uses the existing canonical RECOVERY_REQUIRED (migration-0014 CHECK set); the
        # more specific AMBIGUOUS_EXTERNAL_EFFECT cause is preserved in the audit event + RuntimeResult,
        # never duplicated into a constrained column (no schema/migration change).
        cur.execute(
            "UPDATE execution_attempt SET status = 'FAILED', failure_class = 'RECOVERY_REQUIRED', "
            "updated_at = now() WHERE id = %s",
            (attempt_id,),
        )
        execution._set_status(cur, action_id, "RECOVERY_REQUIRED", actor="factory",
                              reason="AMBIGUOUS_EXTERNAL_EFFECT")
        audit.record_event(
            cur, event_type="execution.ambiguous_external_effect", actor="factory", action_id=action_id,
            payload={"attempt_id": str(attempt_id), **(detail or {})},
        )
    return "RECOVERY_REQUIRED"


def request_dispatch_authorization(
    conn,
    action_request_id: str,
    *,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    approval_ttl_seconds: float = 3600,
):
    """Obtain dispatch authorization for an action whose spec is ALREADY frozen.

    Requires the execution spec to exist first, so the resulting Policy decision
    (and any opened approval) is post-spec. Delegates to the existing Gate 1
    ``request_execution`` — no second policy/approval engine.
    """
    if spec_mod.get_execution_spec(conn, action_request_id) is None:
        raise NotFoundError(
            f"cannot authorize dispatch: no frozen execution spec for action {action_request_id}"
        )
    return execution.request_execution(
        conn, action_request_id, approval_threshold=approval_threshold,
        approval_ttl_seconds=approval_ttl_seconds,
    )


def _assert_spec_bound_authorization(cur, action_request_id, spec_created_at, approval_threshold):
    """Fresh policy evaluation + post-spec approval requirement. Read-only."""
    result, _venture_id, _inp = policy.current_evaluation(cur, action_request_id, approval_threshold)
    if result.decision == "DENY":
        raise ExecutionBlockedError(result.reason)
    if result.decision == "REQUIRE_APPROVAL":
        cur.execute(
            """
            SELECT 1 FROM approval
            WHERE action_request_id = %s
              AND state = 'APPROVED'
              AND bound_inputs_hash = %s
              AND expires_at > now()
              AND requested_at > %s
            LIMIT 1
            """,
            (action_request_id, result.inputs_hash, spec_created_at),
        )
        if cur.fetchone() is None:
            raise ApprovalRequiredError(
                "no valid approval requested after the execution spec was frozen; "
                "pre-spec authorization cannot dispatch this spec"
            )
    return result.decision


def execute_action(
    conn,
    action_request_id: str,
    *,
    registry: WorkerRegistry,
    workspace_ref: Optional[str] = None,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    safety_mode: str = "IDEMPOTENT",
    lease_seconds: float = 300,
    clock=None,
    actor: str = "factory",
) -> RuntimeResult:
    """Dispatch (or retry) the frozen spec's work to a typed worker; capture the result as a claim.

    Retry-aware: refuses a terminal (SUCCEEDED/FAILED) or exhausted action; otherwise
    re-authorizes against the frozen spec (kill switch, policy, budget, approval —
    every attempt) and claims the next execution attempt. A worker exception is a
    WORKER_ERROR and a run exceeding ``execution_spec.timeout_seconds`` (measured by
    ``clock``, default ``time.monotonic``) is a TIMEOUT; both are classified and — if
    the failure is retryable and attempts remain — leave the action re-claimable
    (never SUCCESS). The immutable spec is unchanged across attempts.
    """
    clock = clock or time.monotonic
    row = spec_mod.get_execution_spec(conn, action_request_id)
    if row is None:
        raise NotFoundError(
            f"no frozen execution spec for action {action_request_id}; nothing to dispatch"
        )
    (_id, _arid, venture_id, worker_kind, task_payload, _task_hash, expected_output_contract,
     _verifier_kind, timeout_seconds, max_attempts, capability_scope, spec_hash, created_at) = row

    status = execution.get_status(conn, action_request_id)
    if status in ("SUCCEEDED", "FAILED"):
        raise ExecutionBlockedError(f"action is terminal ({status}); no further attempt")
    # A fail-closed action (a consequential external effect became ambiguous) is NOT auto-claimable:
    # only explicit recovery may resolve it, so ordinary dispatch can never blind-re-issue the effect.
    if status == "RECOVERY_REQUIRED":
        raise ExecutionBlockedError(
            "action is RECOVERY_REQUIRED (ambiguous external effect); explicit recovery required")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (action_request_id,))
        prior_attempts = cur.fetchone()[0]
    if prior_attempts >= max_attempts:
        raise ExecutionBlockedError(f"retry budget exhausted ({prior_attempts}/{max_attempts})")

    # Authorization must apply to the frozen spec, re-checked before EVERY attempt.
    with db.transaction(conn) as cur:
        _assert_spec_bound_authorization(cur, action_request_id, created_at, approval_threshold)

    # Reuse the Gate 1 claim machinery (re-checks kill switch, policy, budget, approval).
    handle = execution.authorize_and_claim(
        conn, action_request_id, safety_mode=safety_mode,
        approval_threshold=approval_threshold, lease_seconds=lease_seconds,
    )

    # Build a bounded worker request. The worker receives NO database connection.
    request = WorkerRequest(
        action_request_id=str(action_request_id),
        attempt_id=str(handle.attempt_id),
        venture_id=str(venture_id),
        spec_hash=spec_hash,
        worker_kind=worker_kind,
        task_payload=dict(task_payload or {}),
        declared_inputs=dict((task_payload or {}).get("declared_inputs", {})),
        capabilities=tuple(capability_scope or ()),
        timeout_seconds=timeout_seconds,
        workspace_ref=workspace_ref or f"mock://isolated/{action_request_id}",
        expected_output_contract=dict(expected_output_contract or {}),
    )

    adapter = registry.get(worker_kind)
    started = clock()
    try:
        result = adapter.execute(request)
    except AmbiguousExternalEffectError as exc:
        # The consequential external boundary was crossed ambiguously and could not be reconciled.
        # This must NEVER auto-retry: fail CLOSED into RECOVERY_REQUIRED (not auto-claimable), so a
        # later attempt can never blind-re-issue the effect. Only explicit recovery may resolve it.
        to_state = _record_ambiguous_recovery_required(
            conn, action_request_id, handle.attempt_id, {"error_type": type(exc).__name__})
        return RuntimeResult(str(action_request_id), str(handle.attempt_id), worker_kind,
                             to_state, dispatched=True, failure_class="AMBIGUOUS_EXTERNAL_EFFECT")
    except WorkerTimeoutError as exc:
        # The adapter's own bounded execution timed out; it terminated its process tree and
        # captured NO result. This is a canonical TIMEOUT (retryable under existing policy),
        # NOT a generic WORKER_ERROR — a blocking/hung provider process is distinguishable and
        # no timed-out output is captured. Retry authority stays with _decide_failure/policy.
        to_state, final_class = _record_failure(
            conn, action_request_id, handle.attempt_id, handle.attempt_number, max_attempts,
            "TIMEOUT", {"error_type": type(exc).__name__},
        )
        return RuntimeResult(str(action_request_id), str(handle.attempt_id), worker_kind,
                             to_state, dispatched=True, failure_class=final_class)
    except Exception as exc:  # a worker fault is a classified machine failure, not a crash
        to_state, final_class = _record_failure(
            conn, action_request_id, handle.attempt_id, handle.attempt_number, max_attempts,
            "WORKER_ERROR", {"error_type": type(exc).__name__},
        )
        return RuntimeResult(str(action_request_id), str(handle.attempt_id), worker_kind,
                             to_state, dispatched=True, failure_class=final_class)
    elapsed = clock() - started
    if elapsed > timeout_seconds:
        # Runtime-observed timeout: the result is discarded (never captured), so a
        # late/over-time result cannot become canonical success for this attempt.
        to_state, final_class = _record_failure(
            conn, action_request_id, handle.attempt_id, handle.attempt_number, max_attempts,
            "TIMEOUT", {"elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds},
        )
        return RuntimeResult(str(action_request_id), str(handle.attempt_id), worker_kind,
                             to_state, dispatched=True, failure_class=final_class)

    # Capture the worker's claim as canonical raw result data (dedup reused). This is
    # NOT canonical success: no proof is created and no status becomes SUCCEEDED.
    raw_payload = {
        "worker_kind": result.worker_kind,
        "worker_version": result.worker_version,
        "reported_outcome": result.reported_outcome,
        "structured_output": result.structured_output,
        "artifacts": list(result.artifacts),
        "failure_metadata": result.failure_metadata,
    }
    execution.record_execution_result(
        conn, action_request_id, external_result_id=result.external_result_id,
        reported_outcome=result.reported_outcome, raw_payload=raw_payload, attempt_id=handle.attempt_id,
    )
    # Capture declared artifacts as append-only provenance (kernel-computed hashes).
    if result.artifacts:
        artifacts_mod.capture_artifacts(
            conn, action_request_id=str(action_request_id), execution_attempt_id=str(handle.attempt_id),
            venture_id=str(venture_id), declarations=[dict(a) for a in result.artifacts], actor=actor,
        )
    with db.transaction(conn) as cur:
        audit.record_event(
            cur, event_type="factory.worker_result_captured", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"attempt_id": str(handle.attempt_id), "worker_kind": result.worker_kind,
                     "external_result_id": result.external_result_id},
        )

    return RuntimeResult(
        action_request_id=str(action_request_id), attempt_id=str(handle.attempt_id),
        worker_kind=result.worker_kind, action_status=execution.get_status(conn, action_request_id),
        dispatched=True, external_result_id=result.external_result_id,
        reported_outcome=result.reported_outcome,
    )


def verify_and_complete(
    conn,
    action_request_id: str,
    *,
    verifier_registry: Optional[VerifierRegistry] = None,
    actual_cost,
    actor: str = "factory",
):
    """Phase 2 — verify a captured attempt deterministically and, only if VERIFIED,
    complete it through the existing canonical proof-gated path.

    The verifier is resolved from the IMMUTABLE ``execution_spec.verifier_kind`` via
    a trusted registry — there is no parameter through which a caller or worker can
    choose a different verifier or a verdict. Verification inputs are reconstructed
    entirely from DURABLE canonical state (``execution_result`` content + immutable
    contract), so a fresh process re-verifies from PostgreSQL alone without
    re-running the worker; the artifact-hash verifier independently RE-HASHES the
    durable content rather than trusting any stored hash. Deterministic
    verification outranks the worker's self-report in both directions. Returns the
    canonical ``CompletionOutcome`` (SUCCEEDED only when VERIFIED).
    """
    registry = verifier_registry if verifier_registry is not None else default_registry()
    row = spec_mod.get_execution_spec(conn, action_request_id)
    if row is None:
        raise NotFoundError(f"no execution spec for action {action_request_id}")
    verifier_kind = row[7]
    expected_output_contract = dict(row[6] or {})
    max_attempts = row[9]
    spec_hash = row[11]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, execution_attempt_id, external_result_id, raw_payload, reported_outcome "
            "FROM execution_result WHERE action_request_id = %s ORDER BY received_at DESC, id DESC LIMIT 1",
            (action_request_id,),
        )
        res = cur.fetchone()
    if res is None:
        raise NotFoundError(f"no captured worker result to verify for action {action_request_id}")
    result_id, attempt_id, external_result_id, raw_payload, reported_outcome = res
    raw_payload = raw_payload or {}
    structured_output = dict(raw_payload.get("structured_output", {}))
    # Durable verification material: the actual artifact content survives in
    # execution_result.raw_payload, so verification is reconstructable after
    # process death from PostgreSQL alone (no worker rerun, no self-verifying hash).
    durable_artifacts = tuple(dict(a) for a in raw_payload.get("artifacts", []))
    with conn.cursor() as cur:
        cur.execute("SELECT attempt_number FROM execution_attempt WHERE id = %s", (attempt_id,))
        attempt_number = cur.fetchone()[0]

    # TRUSTED evidence collection for the TEST_EXECUTION verifier: BEFORE the pure verifier
    # runs, the trusted kernel (this phase, which holds the DB) executes the FROZEN harness
    # against the durable candidate inside the bwrap sandbox and produces machine-owned
    # evidence. subprocess stays OUT of the verifier; the worker supplies no part of this.
    test_evidence = None
    candidate_content_hash = ""
    if verifier_kind == verifiers_mod.TestExecutionVerifier.kind:
        tx = dict((expected_output_contract or {}).get("test_execution", {}))
        candidate_files = {a.get("artifact_key"): a.get("content")
                           for a in durable_artifacts
                           if a.get("artifact_key") and isinstance(a.get("content"), str)}
        candidate_content_hash = test_execution.canonical_candidate_hash(candidate_files)
        evidence = test_execution.run_frozen_tests(
            candidate_files=candidate_files,
            harness_source=str(tx.get("harness_source", "")),
            timeout_seconds=int(tx.get("timeout_seconds", 30)),
        )
        test_evidence = evidence.to_dict()

    # Resolve + run the spec's verifier ONCE over durable data (deterministic).
    request = VerificationRequest(
        action_request_id=str(action_request_id), execution_attempt_id=str(attempt_id),
        verifier_kind=verifier_kind, expected_output_contract=expected_output_contract,
        worker_structured_output=structured_output, artifacts=durable_artifacts, spec_hash=spec_hash,
        # The canonical captured result identity the proof will bind to — the verifier proves THIS
        # object, so it can never verify provider object B while the proof is attached to result A.
        external_result_id=("" if external_result_id is None else str(external_result_id)),
        test_evidence=test_evidence, candidate_content_hash=candidate_content_hash,
    )
    vr = registry.get(verifier_kind).verify(request)  # KeyError on unknown kind -> no receipt, no completion

    # Durably persist the TRANSIENT sandbox execution evidence so a Proof Receipt is
    # historically auditable WITHOUT re-execution: the dynamic fields (terminal/timeout
    # state, exit code, test counts, runner identity/version) survive only in this
    # in-memory evidence, so record them as a kernel-authored, append-only audit event
    # bound to the action/attempt and the receipt's evidence_hash. Bounded safe fields
    # only — never candidate content, prompt, or raw logs. Written for BOTH verdicts so
    # rejected runs are auditable too.
    if test_evidence is not None:
        with db.transaction(conn) as cur:
            audit.record_event(
                cur, event_type="factory.test_execution_evidence", actor=actor,
                action_id=action_request_id,
                payload={
                    "attempt_id": str(attempt_id), "verdict": vr.verdict,
                    "evidence_hash": vr.evidence_hash,
                    "candidate_content_hash": candidate_content_hash,
                    # The FULL machine-owned evidence (bounded safe fields only — no candidate
                    # content, prompt, or raw logs), so the exact evidence-hash preimage is
                    # reconstructable from PostgreSQL alone, with no re-execution.
                    "evidence": dict(test_evidence),
                    # Flat mirror of the key fields for direct human/query auditing.
                    **{k: test_evidence.get(k) for k in (
                        "terminal_state", "exit_code", "harness_result", "tests_total",
                        "tests_passed", "candidate_sha256", "test_sha256", "runner_kind",
                        "runner_version", "bwrap_version", "timeout_seconds")},
                },
            )

    if vr.verdict == "VERIFIED":
        def _record_proof(cur, aid, rid):
            proof_id = proof._write_receipt(
                cur, aid, rid, verdict="VERIFIED", verification_type=vr.verification_type,
                verifier_name=f"gate4.{verifier_kind}", evidence_hash=vr.evidence_hash,
                execution_attempt_id=attempt_id,
            )
            return "VERIFIED", proof_id
        return execution._complete(
            conn, action_request_id, external_result_id=external_result_id,
            reported_outcome=reported_outcome, raw_payload=raw_payload, actual_cost=actual_cost,
            attempt_id=attempt_id, lifecycle_to=None, actor=actor, record_proof=_record_proof,
        )

    # Was this result captured while resolving an ambiguous-effect RECOVERY_REQUIRED attempt? That
    # durable fact (the attempt's own failure_class) is set ONLY by _record_ambiguous_recovery_required.
    with conn.cursor() as cur:
        cur.execute("SELECT failure_class FROM execution_attempt WHERE id = %s", (attempt_id,))
        prior = cur.fetchone()
    recovery_origin = prior is not None and prior[0] == "RECOVERY_REQUIRED"

    if recovery_origin:
        # A rejected RECOVERY verification is NOT ordinary retryable failure: a failed reconciliation
        # is no proof the original effect did not occur. Stay FAIL CLOSED — keep RECOVERY_REQUIRED (never
        # PENDING), keep the attempt's RECOVERY_REQUIRED class, hold the reservation, no auto-retry.
        with db.transaction(conn) as cur:
            proof_id = proof._write_receipt(
                cur, action_request_id, result_id, verdict="FAILED", verification_type=vr.verification_type,
                verifier_name=f"gate4.{verifier_kind}", evidence_hash=vr.evidence_hash,
                execution_attempt_id=attempt_id,
            )
            cur.execute(
                "UPDATE execution_attempt SET status = 'FAILED', failure_class = 'RECOVERY_REQUIRED', "
                "updated_at = now() WHERE id = %s",
                (attempt_id,),
            )
            execution._set_status(cur, action_request_id, "RECOVERY_REQUIRED", actor=actor,
                                  reason="recovery.verification_rejected")
            audit.record_event(
                cur, event_type="execution.recovery_verification_rejected", actor=actor,
                action_id=action_request_id, payload={"verifier_detail": vr.detail},
            )
        return execution.CompletionOutcome("RECOVERY_REQUIRED", proof_id, verified=False, duplicated=False)

    # Ordinary rejected path: record a FAILED proof receipt (history is preserved) and classify the
    # attempt as VERIFICATION_FAILED — retryable while attempts remain — atomically.
    terminal, final_class = _decide_failure("VERIFICATION_FAILED", attempt_number, max_attempts)
    with db.transaction(conn) as cur:
        proof_id = proof._write_receipt(
            cur, action_request_id, result_id, verdict="FAILED", verification_type=vr.verification_type,
            verifier_name=f"gate4.{verifier_kind}", evidence_hash=vr.evidence_hash,
            execution_attempt_id=attempt_id,
        )
        to_state = execution._fail_attempt_cur(
            cur, action_request_id, attempt_id=attempt_id, failure_class=final_class,
            detail={"base_class": "VERIFICATION_FAILED", "verifier_detail": vr.detail},
            terminal=terminal, actor=actor,
        )
    return execution.CompletionOutcome(to_state, proof_id, verified=False, duplicated=False)


def resume_action(
    conn,
    action_request_id: str,
    *,
    registry: Optional[WorkerRegistry] = None,
    verifier_registry: Optional[VerifierRegistry] = None,
    actual_cost,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    safety_mode: str = "IDEMPOTENT",
    lease_seconds: float = 300,
    clock=None,
    actor: str = "factory",
) -> dict:
    """Resume a possibly-interrupted action from DURABLE PostgreSQL state alone.

    All recovery obeys the canonical Gate 4 rules — immutable spec, max_attempts, and
    current authorization:

    - a durably captured result awaiting verification is verified + completed WITHOUT
      re-dispatching the worker (completion re-checks current governance);
    - a crashed CLAIMED attempt whose lease expired and which never persisted a result
      is abandoned by safety mode: UNSAFE -> RECOVERY_REQUIRED (no auto-rerun);
      IDEMPOTENT/RECONCILABLE -> the action returns to a claimable state and, when a
      registry is supplied and attempts remain, a FRESH AUTHORIZED attempt is
      dispatched via ``execute_action`` (which re-evaluates policy/kill/budget/approval
      and enforces max_attempts). If the crashed attempt was the last budgeted one,
      the action is terminal FAILED — recovery never exceeds max_attempts.

    Gate 1's ``recovery.recover_action`` remains the primitive for non-spec actions.
    """
    row = spec_mod.get_execution_spec(conn, action_request_id)
    if row is None:
        raise NotFoundError(f"no execution spec for action {action_request_id}")
    max_attempts = row[9]

    status = execution.get_status(conn, action_request_id)
    if status == "SUCCEEDED":
        return {"outcome": "already_succeeded", "status": status}
    if status == "FAILED":
        return {"outcome": "terminally_failed", "status": status}

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (action_request_id,))
        has_result = cur.fetchone()[0] > 0
        already_verified = proof.verified_proof_id(cur, action_request_id) is not None
        cur.execute(
            "SELECT id, status, attempt_number, safety_mode, lease_expires_at <= now() "
            "FROM execution_attempt WHERE action_request_id = %s ORDER BY attempt_number DESC LIMIT 1",
            (action_request_id,),
        )
        latest = cur.fetchone()

    # A durable result awaiting verification -> verify from durable state (no worker rerun).
    if has_result and not already_verified:
        out = verify_and_complete(
            conn, action_request_id, verifier_registry=verifier_registry,
            actual_cost=actual_cost, actor=actor,
        )
        return {"outcome": "verified_from_durable_state", "status": out.status, "verified": out.verified}

    if latest is None:
        return {"outcome": "no_attempt", "status": status}
    attempt_id, attempt_status, attempt_number, attempt_safety, lease_expired = latest

    # A crashed CLAIMED attempt (expired lease, no result).
    if attempt_status == "CLAIMED" and lease_expired:
        if attempt_safety == "UNSAFE":
            with db.transaction(conn) as cur:
                cur.execute(
                    "UPDATE execution_attempt SET status = 'RECOVERY_REQUIRED', failure_class = 'RECOVERY_REQUIRED', "
                    "finished_at = now(), updated_at = now() WHERE id = %s", (attempt_id,))
                execution._set_status(cur, action_request_id, "RECOVERY_REQUIRED", actor=actor, reason="unsafe_ambiguous")
            return {"outcome": "recovery_required", "status": "RECOVERY_REQUIRED"}
        if attempt_number >= max_attempts:  # the crashed attempt was the last budgeted -> terminal
            with db.transaction(conn) as cur:
                cur.execute(
                    "UPDATE execution_attempt SET status = 'ABANDONED', failure_class = 'RETRY_EXHAUSTED', "
                    "finished_at = now(), updated_at = now() WHERE id = %s", (attempt_id,))
                budget._release(cur, action_request_id)
                execution._set_status(cur, action_request_id, "FAILED", actor=actor, reason="recovery_exhausted")
            return {"outcome": "recovery_exhausted", "status": "FAILED"}
        # attempts remain: abandon the stale attempt and return the action to claimable.
        with db.transaction(conn) as cur:
            cur.execute(
                "UPDATE execution_attempt SET status = 'ABANDONED', finished_at = now(), updated_at = now() "
                "WHERE id = %s", (attempt_id,))
            execution._set_status(cur, action_request_id, "PENDING", actor=actor, reason="recovered_retryable")
        if registry is None:
            return {"outcome": "recovered_pending", "status": "PENDING"}
        # Dispatch a fresh AUTHORIZED attempt through the canonical path (reauth + max_attempts).
        r = execute_action(
            conn, action_request_id, registry=registry, approval_threshold=approval_threshold,
            safety_mode=safety_mode, lease_seconds=lease_seconds, clock=clock, actor=actor,
        )
        return {"outcome": "recovered_and_dispatched", "status": r.action_status,
                "dispatched": r.dispatched, "failure_class": r.failure_class}

    if attempt_status == "CLAIMED":
        return {"outcome": "lease_active", "status": status}
    return {"outcome": "no_result_to_resume", "status": status}
