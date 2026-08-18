"""Execution authorization, claiming, raw results, and canonical completion.

This is the durable canonical protocol around a *simulated/replaceable*
executor — not a worker runtime, queue or adapter. Canonical success is
established only in one atomic transaction, only after a deterministic VERIFIED
Proof Receipt, and only once.

Exactly-once semantics: Gate 1 guarantees exactly-once **canonical completion**
(via the VERIFIED-proof partial unique + guarded transitions). Exactly-once
external effect depends on the executor's safety mode (see recovery.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from psycopg.types.json import Json

from . import audit, budget, db, lifecycle, policy, proof
from .actions import canonical_payload_hash
from .approvals import create_pending, valid_approval
from .errors import (
    ApprovalRequiredError,
    ExecutionBlockedError,
    InconsistentCanonicalStateError,
    NotFoundError,
)

# ActionRequest run-status transitions permitted through the generic guarded
# path. Note: (RUNNING -> SUCCEEDED) is deliberately ABSENT — canonical success
# is reachable only through _transition_to_success(), which is proof-gated.
_ALLOWED_STATUS = frozenset(
    {
        ("PENDING", "AWAITING_APPROVAL"),
        ("PENDING", "RUNNING"),
        ("PENDING", "CANCELLED"),
        ("AWAITING_APPROVAL", "RUNNING"),
        ("AWAITING_APPROVAL", "CANCELLED"),
        ("RUNNING", "FAILED"),
        ("RUNNING", "RECOVERY_REQUIRED"),
        ("RECOVERY_REQUIRED", "RUNNING"),
        ("RECOVERY_REQUIRED", "FAILED"),
    }
)
_CLAIMABLE = {"PENDING", "AWAITING_APPROVAL", "RECOVERY_REQUIRED"}


def _set_status(cur, action_id: str, to_state: str, *, actor: str, reason: Optional[str] = None) -> None:
    cur.execute("SELECT status FROM action_request WHERE id = %s FOR UPDATE", (action_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"action_request {action_id} does not exist")
    current = row[0]
    if current == to_state:
        return  # idempotent no-op
    if (current, to_state) not in _ALLOWED_STATUS:
        raise ExecutionBlockedError(f"illegal status transition {current} -> {to_state}")
    cur.execute(
        "UPDATE action_request SET status = %s, updated_at = now() WHERE id = %s",
        (to_state, action_id),
    )
    audit.record_event(
        cur, event_type="action.status_changed", actor=actor, action_id=action_id,
        payload={"from": current, "to": to_state, "reason": reason},
    )


def _transition_to_success(cur, action_id: str, *, actor: str) -> None:
    """The ONLY path to canonical SUCCEEDED. Requires a matching VERIFIED proof.

    Validates that a VERIFIED proof receipt belonging to *this* ActionRequest
    exists before moving RUNNING -> SUCCEEDED. A VERIFIED receipt for another
    action, a FAILED receipt, or no receipt all fail deterministically.
    """
    if proof.verified_proof_id(cur, action_id) is None:
        raise ExecutionBlockedError(
            "canonical success requires a VERIFIED proof receipt for this action"
        )
    cur.execute("SELECT status FROM action_request WHERE id = %s FOR UPDATE", (action_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"action_request {action_id} does not exist")
    current = row[0]
    if current == "SUCCEEDED":
        return
    if current != "RUNNING":
        raise ExecutionBlockedError(f"cannot transition {current} -> SUCCEEDED")
    cur.execute(
        "UPDATE action_request SET status = 'SUCCEEDED', updated_at = now() WHERE id = %s",
        (action_id,),
    )
    audit.record_event(
        cur, event_type="action.status_changed", actor=actor, action_id=action_id,
        payload={"from": current, "to": "SUCCEEDED"},
    )


@dataclass(frozen=True)
class RequestOutcome:
    decision: str
    reason: str
    policy_decision_id: str
    approval_id: Optional[str]


def request_execution(
    conn,
    action_id: str,
    *,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    approval_ttl_seconds: float = 3600,
) -> RequestOutcome:
    """Evaluate policy and, if approval is required, open a bound approval."""
    with db.transaction(conn) as cur:
        result, venture_id, inp = policy.current_evaluation(cur, action_id, approval_threshold)
        decision_id = policy.persist_decision(cur, action_id, venture_id, result, inp)
        approval_id = None
        if result.decision == "REQUIRE_APPROVAL":
            approval_id = create_pending(
                cur, action_id, decision_id, result.inputs_hash, ttl_seconds=approval_ttl_seconds
            )
            _set_status(cur, action_id, "AWAITING_APPROVAL", actor="policy_engine")
        return RequestOutcome(result.decision, result.reason, decision_id, approval_id)


@dataclass(frozen=True)
class AttemptHandle:
    attempt_id: str
    execution_key: str
    attempt_number: int
    lease_token: str


def _create_attempt(cur, action_id: str, safety_mode: str, executor_ref: str, lease_seconds: float) -> AttemptHandle:
    execution_key = f"exec:{action_id}"
    cur.execute(
        "SELECT coalesce(max(attempt_number), 0) + 1 FROM execution_attempt WHERE execution_key = %s",
        (execution_key,),
    )
    attempt_number = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO execution_attempt
            (action_request_id, execution_key, attempt_number, executor_ref, safety_mode, lease_expires_at)
        VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))
        RETURNING id, lease_token
        """,
        (action_id, execution_key, attempt_number, executor_ref, safety_mode, lease_seconds),
    )
    attempt_id, lease_token = cur.fetchone()
    return AttemptHandle(attempt_id, execution_key, attempt_number, lease_token)


def authorize_and_claim(
    conn,
    action_id: str,
    *,
    safety_mode: str,
    executor_ref: str = "sim",
    lease_seconds: float = 300,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
) -> AttemptHandle:
    """Recheck policy/kill/budget, require valid approval if needed, reserve and claim.

    Atomic: only one claimer wins; concurrent claims see a non-claimable state.
    """
    with db.transaction(conn) as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s FOR UPDATE", (action_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"action_request {action_id} does not exist")
        if row[0] not in _CLAIMABLE:
            raise ExecutionBlockedError(f"action not claimable in status {row[0]}")

        # Recheck current policy (kill switch, budget, autonomy) at execution time.
        result, venture_id, _inp = policy.current_evaluation(cur, action_id, approval_threshold)
        if result.decision == "DENY":
            raise ExecutionBlockedError(result.reason)
        if result.decision == "REQUIRE_APPROVAL":
            if valid_approval(cur, action_id, result.inputs_hash) is None:
                raise ApprovalRequiredError(
                    "no valid, non-expired approval for the current policy state"
                )

        # Reserve budget atomically at claim time (not while waiting for approval).
        budget._reserve(cur, action_id)

        handle = _create_attempt(cur, action_id, safety_mode, executor_ref, lease_seconds)
        _set_status(cur, action_id, "RUNNING", actor="executor")
        audit.record_event(
            cur, event_type="execution.claimed", actor="executor", venture_id=venture_id,
            action_id=action_id, payload={"attempt": handle.attempt_number, "safety_mode": safety_mode},
        )
        return handle


def _upsert_result(cur, action_id, attempt_id, external_result_id, reported_outcome, raw_payload):
    raw_hash = canonical_payload_hash(raw_payload or {})
    cur.execute(
        """
        INSERT INTO execution_result
            (action_request_id, execution_attempt_id, external_result_id, raw_payload, raw_hash, reported_outcome)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (action_request_id, external_result_id) DO NOTHING
        RETURNING id
        """,
        (action_id, attempt_id, external_result_id, Json(raw_payload or {}), raw_hash, reported_outcome),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0], True
    cur.execute(
        "SELECT id FROM execution_result WHERE action_request_id = %s AND external_result_id = %s",
        (action_id, external_result_id),
    )
    return cur.fetchone()[0], False


def record_execution_result(
    conn,
    action_id: str,
    *,
    external_result_id: str,
    reported_outcome: str,
    raw_payload: Optional[dict[str, Any]] = None,
    attempt_id: Optional[str] = None,
) -> tuple:
    """Store a raw executor result once (deduped). This is NOT canonical success."""
    with db.transaction(conn) as cur:
        result_id, created = _upsert_result(
            cur, action_id, attempt_id, external_result_id, reported_outcome, raw_payload
        )
        if created:
            audit.record_event(
                cur, event_type="execution.result_recorded", actor="executor",
                action_id=action_id, payload={"external_result_id": external_result_id},
            )
        return result_id, created


@dataclass(frozen=True)
class CompletionOutcome:
    status: str          # SUCCEEDED or FAILED
    proof_id: str
    verified: bool
    duplicated: bool


def complete_execution(
    conn,
    action_id: str,
    *,
    external_result_id: str,
    reported_outcome: str,
    raw_payload: Optional[dict[str, Any]],
    actual_cost,
    attempt_id: Optional[str] = None,
    lifecycle_to: Optional[str] = None,
    actor: str = "executor",
) -> CompletionOutcome:
    """The canonical Gate 1 success transaction (deterministic token-match proof).

    Public completion accepts no verifier/verdict: the verification logic is fixed
    (the built-in deterministic token verifier). An action that carries a Gate 4
    ``execution_spec`` cannot be completed through this path — it must be verified
    by its immutable spec verifier via ``factory.runtime.verify_and_complete`` — so
    the token-match path cannot bypass a spec's chosen verifier.
    """
    return _complete(
        conn, action_id, external_result_id=external_result_id, reported_outcome=reported_outcome,
        raw_payload=raw_payload, actual_cost=actual_cost, attempt_id=attempt_id,
        lifecycle_to=lifecycle_to, actor=actor, record_proof=None,
    )


def _complete(
    conn,
    action_id: str,
    *,
    external_result_id: str,
    reported_outcome: str,
    raw_payload: Optional[dict[str, Any]],
    actual_cost,
    attempt_id: Optional[str],
    lifecycle_to: Optional[str],
    actor: str,
    record_proof=None,
) -> CompletionOutcome:
    """Shared completion core. ``record_proof(cur, action_id, result_id) -> (verdict,
    proof_id)`` is an INTERNAL trusted hook: when None, the built-in deterministic
    token verifier is used; the Gate 4 runtime supplies one that resolves the
    verifier from the immutable spec. There is no public parameter through which a
    caller can inject verification code or a verdict.
    """
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT venture_id, status FROM action_request WHERE id = %s FOR UPDATE", (action_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"action_request {action_id} does not exist")
        venture_id, status = row

        # An action with a Gate 4 execution spec must be completed through that
        # spec's verifier (record_proof supplied); the default token path may not
        # complete it. This closes the token-match bypass of a spec's verifier.
        cur.execute("SELECT 1 FROM execution_spec WHERE action_request_id = %s", (action_id,))
        if cur.fetchone() is not None and record_proof is None:
            raise ExecutionBlockedError(
                "action has an execution spec; complete it through its spec verifier "
                "(factory.runtime.verify_and_complete), not the token path"
            )

        # Duplicate completion: canonical success is inferred only when BOTH the
        # SUCCEEDED status AND a matching VERIFIED proof receipt agree. If exactly
        # one is present, the canonical state is inconsistent -> fail; never
        # silently repair or report success from one side alone.
        existing = proof.verified_proof_id(cur, action_id)
        is_succeeded = status == "SUCCEEDED"
        if existing is not None or is_succeeded:
            if existing is not None and is_succeeded:
                return CompletionOutcome("SUCCEEDED", existing, verified=True, duplicated=True)
            raise InconsistentCanonicalStateError(
                f"inconsistent canonical success for {action_id}: "
                f"verified_proof={existing is not None}, status_succeeded={is_succeeded}"
            )

        # 1. accept/dedup raw result.
        result_id, _created = _upsert_result(
            cur, action_id, attempt_id, external_result_id, reported_outcome, raw_payload
        )

        # 2/3. deterministic verification + receipt (verdict derived internally,
        # never caller-supplied). Gate 4 supplies a spec-resolved record_proof.
        if record_proof is None:
            verdict, proof_id = proof._record_receipt(
                cur, action_id, result_id, reported_outcome, raw_payload,
                execution_attempt_id=attempt_id,
            )
        else:
            verdict, proof_id = record_proof(cur, action_id, result_id)

        if verdict != "VERIFIED":
            budget._release(cur, action_id)  # unblock reserved funds on failure
            _set_status(cur, action_id, "FAILED", actor=actor, reason="proof_failed")
            if attempt_id is not None:
                cur.execute(
                    "UPDATE execution_attempt SET status = 'FAILED', updated_at = now() WHERE id = %s",
                    (attempt_id,),
                )
            audit.record_event(
                cur, event_type="execution.failed", actor=actor, venture_id=venture_id,
                action_id=action_id, payload={"reason": "proof_failed"},
            )
            return CompletionOutcome("FAILED", proof_id, verified=False, duplicated=False)

        # 4. transition to canonical success (proof-gated: validates the VERIFIED
        # receipt just recorded for this action).
        _transition_to_success(cur, action_id, actor=actor)
        # 5/6. reconcile budget (commit actual, release unused; reject overspend).
        budget.reconcile_completion(cur, action_id, actual_cost)
        # 7. authorized lifecycle transition, only if requested and permitted.
        if lifecycle_to is not None:
            lifecycle.transition_cur(cur, venture_id, lifecycle_to, actor=actor)
        if attempt_id is not None:
            cur.execute(
                "UPDATE execution_attempt SET status = 'COMPLETED', updated_at = now() WHERE id = %s",
                (attempt_id,),
            )
        # 8. success audit.
        audit.record_event(
            cur, event_type="execution.succeeded", actor=actor, venture_id=venture_id,
            action_id=action_id, payload={"proof_id": str(proof_id)},
        )
        return CompletionOutcome("SUCCEEDED", proof_id, verified=True, duplicated=False)


def get_status(conn, action_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (action_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"action_request {action_id} does not exist")
        return row[0]
