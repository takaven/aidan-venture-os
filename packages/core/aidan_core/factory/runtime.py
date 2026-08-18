"""Factory runtime — durable, claim-only worker dispatch (Gate 4, Slice 1).

``execute_action`` drives an already-governed ActionRequest against its frozen,
immutable execution spec: it re-checks authorization *against that spec*, claims
a canonical execution attempt through the existing Gate 1 machinery, dispatches a
typed replaceable worker (with no DB access), and captures the worker's result as
a CLAIM into the existing execution_result table. It STOPS there — no verifier,
no Proof Receipt, no canonical SUCCESS, no retries, no timeouts (later slices).

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

from .. import audit, db, execution, policy, proof
from ..errors import ApprovalRequiredError, ExecutionBlockedError, NotFoundError
from . import artifacts as artifacts_mod
from . import spec as spec_mod
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

    # Resolve + run the spec's verifier ONCE over durable data (deterministic).
    request = VerificationRequest(
        action_request_id=str(action_request_id), execution_attempt_id=str(attempt_id),
        verifier_kind=verifier_kind, expected_output_contract=expected_output_contract,
        worker_structured_output=structured_output, artifacts=durable_artifacts, spec_hash=spec_hash,
    )
    vr = registry.get(verifier_kind).verify(request)  # KeyError on unknown kind -> no receipt, no completion

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

    # Rejected: record a FAILED proof receipt (history is preserved) and classify the
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
    verifier_registry: Optional[VerifierRegistry] = None,
    actual_cost,
    actor: str = "factory",
) -> dict:
    """Resume a possibly-interrupted action from DURABLE PostgreSQL state alone.

    A fresh process (no in-memory WorkerResult, no runtime object) can call this to
    finish an attempt whose worker result was already captured before a crash: it
    verifies from durable state and completes WITHOUT re-dispatching the worker.
    Terminal actions are reported as-is. Crashed attempts that never persisted a
    result (lease/claim recovery) remain the province of the existing
    ``recovery.recover_action`` safety-mode machinery, which callers invoke directly.
    """
    status = execution.get_status(conn, action_request_id)
    if status == "SUCCEEDED":
        return {"outcome": "already_succeeded", "status": status}
    if status == "FAILED":
        return {"outcome": "terminally_failed", "status": status}

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (action_request_id,))
        has_result = cur.fetchone()[0] > 0
        already_verified = proof.verified_proof_id(cur, action_request_id) is not None

    if has_result and not already_verified:
        out = verify_and_complete(
            conn, action_request_id, verifier_registry=verifier_registry,
            actual_cost=actual_cost, actor=actor,
        )
        return {"outcome": "verified_from_durable_state", "status": out.status, "verified": out.verified}
    return {"outcome": "no_result_to_resume", "status": status}
