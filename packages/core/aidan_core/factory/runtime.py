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

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .. import audit, db, execution, policy
from ..errors import ApprovalRequiredError, ExecutionBlockedError, NotFoundError
from . import artifacts as artifacts_mod
from . import spec as spec_mod
from .verifiers import VerificationRequest, VerifierRegistry
from .workers import WorkerRegistry, WorkerRequest


@dataclass(frozen=True)
class RuntimeResult:
    action_request_id: str
    attempt_id: str
    external_result_id: str
    worker_kind: str
    reported_outcome: str
    action_status: str          # post-dispatch canonical status (RUNNING; never SUCCEEDED here)
    dispatched: bool


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
    actor: str = "factory",
) -> RuntimeResult:
    """Dispatch the frozen spec's work to a typed worker; capture the result as a claim.

    Never verifies, proves, or completes. The worker's reported outcome is a claim
    only and cannot reach canonical SUCCESS (also DB-enforced by 0012).
    """
    row = spec_mod.get_execution_spec(conn, action_request_id)
    if row is None:
        raise NotFoundError(
            f"no frozen execution spec for action {action_request_id}; nothing to dispatch"
        )
    (_id, _arid, venture_id, worker_kind, task_payload, _task_hash, expected_output_contract,
     _verifier_kind, timeout_seconds, _max_attempts, capability_scope, spec_hash, created_at) = row

    # Authorization must apply to the frozen spec (checked before any claim/dispatch).
    with db.transaction(conn) as cur:
        _assert_spec_bound_authorization(cur, action_request_id, created_at, approval_threshold)

    # Reuse the Gate 1 claim machinery (kill switch, budget, approval, attempt).
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
    result = adapter.execute(request)

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
    # This is provenance only — it never implies verification or success.
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
        action_request_id=str(action_request_id),
        attempt_id=str(handle.attempt_id),
        external_result_id=result.external_result_id,
        worker_kind=result.worker_kind,
        reported_outcome=result.reported_outcome,
        action_status=execution.get_status(conn, action_request_id),
        dispatched=True,
    )


def verify_and_complete(
    conn,
    action_request_id: str,
    *,
    verifier_registry: VerifierRegistry,
    actual_cost,
    actor: str = "factory",
):
    """Phase 2 — verify a captured attempt deterministically and, only if VERIFIED,
    complete it through the existing canonical proof-gated path.

    The verifier is selected by the IMMUTABLE ``execution_spec.verifier_kind`` (the
    caller cannot override it) and runs over canonical inputs (contract, captured
    worker result, captured artifacts) with no DB access. Its verdict is derived —
    never caller-supplied — and is converted into the one canonical Proof Receipt
    by the existing proof authority. Deterministic verification outranks the
    worker's self-report in both directions. Returns the canonical
    ``CompletionOutcome`` (SUCCEEDED only when VERIFIED).
    """
    row = spec_mod.get_execution_spec(conn, action_request_id)
    if row is None:
        raise NotFoundError(f"no execution spec for action {action_request_id}")
    verifier_kind = row[7]
    expected_output_contract = dict(row[6] or {})
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
    _res_id, attempt_id, external_result_id, raw_payload, reported_outcome = res
    structured_output = dict((raw_payload or {}).get("structured_output", {}))

    # Pre-load canonical artifacts so the verifier receives DATA, never a connection.
    captured = tuple(artifacts_mod.get_artifacts(conn, attempt_id))

    def _verify(_cur, aid, _result_id):
        request = VerificationRequest(
            action_request_id=str(aid), execution_attempt_id=str(attempt_id),
            verifier_kind=verifier_kind, expected_output_contract=expected_output_contract,
            worker_structured_output=structured_output, artifacts=captured, spec_hash=spec_hash,
        )
        verifier = verifier_registry.get(verifier_kind)  # KeyError -> deterministic failure, no receipt
        vr = verifier.verify(request)
        proof_verdict = "VERIFIED" if vr.verdict == "VERIFIED" else "FAILED"
        return proof_verdict, vr.verification_type, vr.evidence_hash

    return execution.complete_execution(
        conn, action_request_id, external_result_id=external_result_id,
        reported_outcome=reported_outcome, raw_payload=raw_payload, actual_cost=actual_cost,
        attempt_id=attempt_id, verifier=_verify, verifier_name=f"gate4.{verifier_kind}", actor=actor,
    )
