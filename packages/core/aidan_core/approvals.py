"""Durable approvals bound to an exact policy decision.

An approval authorises exactly the policy-decision input state it was created
for (``bound_inputs_hash``). PENDING is the only mutable state; APPROVED /
REJECTED / EXPIRED are terminal and enforced by a DB trigger. Expiry governs
execution validity: an APPROVED approval past ``expires_at`` cannot authorise
execution (checked at execution time). No notifications/UI/human-workflow.
"""
from __future__ import annotations

from typing import Optional

from . import audit, db
from .errors import ApprovalStateError


def create_pending(
    cur,
    action_request_id: str,
    policy_decision_id: str,
    bound_inputs_hash: str,
    *,
    ttl_seconds: float = 3600,
) -> str:
    """Create a PENDING approval (cursor-based). Returns its id."""
    cur.execute(
        """
        INSERT INTO approval
            (action_request_id, policy_decision_id, bound_inputs_hash, expires_at)
        VALUES (%s, %s, %s, now() + make_interval(secs => %s))
        RETURNING id
        """,
        (action_request_id, policy_decision_id, bound_inputs_hash, ttl_seconds),
    )
    approval_id = cur.fetchone()[0]
    audit.record_event(
        cur, event_type="approval.requested", actor="policy_engine",
        action_id=action_request_id, payload={"approval_id": str(approval_id)},
    )
    return approval_id


def valid_approval(cur, action_request_id: str, inputs_hash: str):
    """Return a currently-valid APPROVED approval bound to ``inputs_hash``, else None.

    Valid == state APPROVED, bound to the exact current inputs hash, not expired.
    """
    cur.execute(
        """
        SELECT id FROM approval
        WHERE action_request_id = %s
          AND state = 'APPROVED'
          AND bound_inputs_hash = %s
          AND expires_at > now()
        ORDER BY decided_at DESC
        LIMIT 1
        """,
        (action_request_id, inputs_hash),
    )
    return cur.fetchone()


def _decide(conn, approval_id: str, target_state: str, decided_by: str, reason: Optional[str]) -> str:
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT state, action_request_id FROM approval WHERE id = %s FOR UPDATE",
            (approval_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ApprovalStateError(f"approval {approval_id} does not exist")
        state, action_id = row
        if state == target_state:
            return state  # idempotent duplicate decision
        if state != "PENDING":
            raise ApprovalStateError(f"approval is terminal ({state}); cannot set {target_state}")

        cur.execute(
            "UPDATE approval SET state = %s, decided_by = %s, decided_at = now(), reason = %s "
            "WHERE id = %s",
            (target_state, decided_by, reason, approval_id),
        )
        audit.record_event(
            cur, event_type=f"approval.{target_state.lower()}", actor=decided_by,
            action_id=action_id, payload={"approval_id": str(approval_id)},
        )
    return target_state


def approve(conn, approval_id: str, *, decided_by: str, reason: Optional[str] = None) -> str:
    return _decide(conn, approval_id, "APPROVED", decided_by, reason)


def reject(conn, approval_id: str, *, decided_by: str, reason: Optional[str] = None) -> str:
    return _decide(conn, approval_id, "REJECTED", decided_by, reason)


def expire_if_due(conn, approval_id: str) -> str:
    """Flip a PENDING approval to EXPIRED if past its deadline. Returns state."""
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT state, action_request_id, expires_at <= now() AS due "
            "FROM approval WHERE id = %s FOR UPDATE",
            (approval_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ApprovalStateError(f"approval {approval_id} does not exist")
        state, action_id, due = row
        if state != "PENDING" or not due:
            return state
        cur.execute(
            "UPDATE approval SET state = 'EXPIRED', decided_at = now() WHERE id = %s",
            (approval_id,),
        )
        audit.record_event(
            cur, event_type="approval.expired", actor="system",
            action_id=action_id, payload={"approval_id": str(approval_id)},
        )
    return "EXPIRED"


def get_approval(conn, approval_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, action_request_id, policy_decision_id, state, bound_inputs_hash, "
            "requested_at, expires_at, decided_by, decided_at, reason FROM approval WHERE id = %s",
            (approval_id,),
        )
        return cur.fetchone()
