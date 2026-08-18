"""Immutable execution specifications bound 1:1 to a governed ActionRequest.

An execution_spec freezes the executable work, capability boundary and future
verification contract for an ActionRequest. It is created by trusted
kernel/operator integration BEFORE the authorization used for dispatch — never
inferred from worker output — and it is immutable: correcting the task means a
new governed ActionRequest/spec, never a mutation. The Gate 4 runtime re-checks
authorization against the frozen spec, so authorization that predates the spec
cannot authorize its dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError

# Finite capability vocabulary for Gate 4 Alpha (mirrors the DB CHECK).
CAPABILITIES = frozenset(
    {"READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "RUN_TESTS", "PRODUCE_PATCH", "READ_DECLARED_INPUTS"}
)


@dataclass(frozen=True)
class SpecResult:
    spec_id: str
    spec_hash: str
    created: bool


def compute_spec_hash(
    *, worker_kind: str, task_payload: dict, expected_output_contract: dict,
    verifier_kind: str, timeout_seconds: int, max_attempts: int, capability_scope: list,
) -> str:
    """Deterministic identity over the spec's executable authority.

    Covers everything that defines what work is authorized and how it will be
    judged. Excludes DB-generated ids and timestamps. Capabilities are sorted so
    ordering is not semantic. Ints are used directly (no Decimal normalization).
    """
    return canonical_payload_hash({
        "worker_kind": worker_kind,
        "task_payload": task_payload,
        "expected_output_contract": expected_output_contract,
        "verifier_kind": verifier_kind,
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
        "capability_scope": sorted(capability_scope),
    })


def create_execution_spec(
    conn,
    action_request_id: str,
    *,
    worker_kind: str,
    verifier_kind: str,
    timeout_seconds: int,
    max_attempts: int,
    capability_scope: list,
    task_payload: Optional[dict[str, Any]] = None,
    expected_output_contract: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> SpecResult:
    """Freeze the execution spec for a governed ActionRequest. Idempotent per action.

    Reuse with an identical spec converges; a changed spec under the same
    ActionRequest is a hard conflict (never a silent mutation).
    """
    if not worker_kind:
        raise ValueError("worker_kind is required")
    if not verifier_kind:
        raise ValueError("verifier_kind is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    caps = list(capability_scope or [])
    unknown = set(caps) - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown capabilities {sorted(unknown)}; allowed: {sorted(CAPABILITIES)}")

    task_payload = task_payload or {}
    expected_output_contract = expected_output_contract or {}
    task_hash = canonical_payload_hash(task_payload)
    spec_hash = compute_spec_hash(
        worker_kind=worker_kind, task_payload=task_payload,
        expected_output_contract=expected_output_contract, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts, capability_scope=caps,
    )

    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM action_request WHERE id = %s", (action_request_id,))
        arow = cur.fetchone()
        if arow is None:
            raise NotFoundError(f"action_request {action_request_id} does not exist")
        venture_id = arow[0]

        cur.execute(
            "SELECT id, spec_hash FROM execution_spec WHERE action_request_id = %s",
            (action_request_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != spec_hash:
                raise IdempotencyConflictError(
                    f"execution_spec for action {action_request_id} already exists with different content"
                )
            return SpecResult(existing[0], spec_hash, created=False)

        cur.execute(
            """
            INSERT INTO execution_spec
                (action_request_id, venture_id, worker_kind, task_payload, task_hash,
                 expected_output_contract, verifier_kind, timeout_seconds, max_attempts,
                 capability_scope, spec_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (action_request_id, venture_id, worker_kind, Json(task_payload), task_hash,
             Json(expected_output_contract), verifier_kind, timeout_seconds, max_attempts,
             caps, spec_hash),
        )
        spec_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="factory.execution_spec_created", actor=actor, venture_id=venture_id,
            action_id=action_request_id, payload={"spec_id": str(spec_id), "spec_hash": spec_hash},
        )
    return SpecResult(spec_id, spec_hash, created=True)


def get_execution_spec(conn, action_request_id: str):
    """Return the frozen spec row for an action, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, action_request_id, venture_id, worker_kind, task_payload, task_hash, "
            "expected_output_contract, verifier_kind, timeout_seconds, max_attempts, "
            "capability_scope, spec_hash, created_at FROM execution_spec WHERE action_request_id = %s",
            (action_request_id,),
        )
        return cur.fetchone()
