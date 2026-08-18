"""Crash/restart recovery for claimed-but-unfinished executions.

Recovery decisions depend on the attempt's safety mode:

- IDEMPOTENT   — an expired lease can be safely reclaimed (same execution key).
- RECONCILABLE — query a deterministic reconciler first; if the external effect
                 already happened, record its result so completion can proceed;
                 otherwise reclaim.
- UNSAFE       — never auto-retry an ambiguous attempt; move to a durable
                 RECOVERY_REQUIRED state for human/By-hand resolution.

If a raw result already exists (crash after the external effect but before
canonical completion), recovery surfaces it so the normal completion path — the
only path to canonical success — can run and converge exactly once.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import audit, db, execution


def recover_action(
    conn,
    action_id: str,
    *,
    reconciler: Optional[Callable[[str], Optional[dict]]] = None,
    lease_seconds: float = 300,
) -> dict:
    """Attempt recovery of the latest execution attempt for an action."""
    with db.transaction(conn) as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s FOR UPDATE", (action_id,))
        arow = cur.fetchone()
        if arow is None:
            return {"outcome": "no_action"}
        action_status = arow[0]

        cur.execute(
            """
            SELECT id, safety_mode, status, lease_expires_at <= now() AS expired
            FROM execution_attempt
            WHERE action_request_id = %s
            ORDER BY attempt_number DESC
            LIMIT 1
            """,
            (action_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {"outcome": "no_attempt"}
        attempt_id, safety_mode, attempt_status, expired = row

        if attempt_status != "CLAIMED":
            return {"outcome": "not_active", "attempt_status": attempt_status}

        # A raw result already exists -> external effect happened; let completion
        # run (regardless of lease state); free the active claim.
        cur.execute(
            "SELECT count(*) FROM execution_result WHERE action_request_id = %s", (action_id,)
        )
        if cur.fetchone()[0] > 0:
            cur.execute(
                "UPDATE execution_attempt SET status = 'ABANDONED', updated_at = now() WHERE id = %s",
                (attempt_id,),
            )
            audit.record_event(
                cur, event_type="recovery.result_present", actor="recovery", action_id=action_id,
            )
            return {"outcome": "result_present"}

        if not expired:
            return {"outcome": "lease_active"}

        if safety_mode == "UNSAFE":
            cur.execute(
                "UPDATE execution_attempt SET status = 'RECOVERY_REQUIRED', updated_at = now() "
                "WHERE id = %s",
                (attempt_id,),
            )
            execution._set_status(cur, action_id, "RECOVERY_REQUIRED", actor="recovery")
            audit.record_event(
                cur, event_type="recovery.required", actor="recovery", action_id=action_id,
                payload={"reason": "unsafe_ambiguous"},
            )
            return {"outcome": "recovery_required"}

        if safety_mode == "RECONCILABLE" and reconciler is not None:
            ext = reconciler(action_id)
            if ext is not None:
                execution._upsert_result(
                    cur, action_id, attempt_id, ext["external_result_id"],
                    ext.get("reported_outcome", "success"), ext.get("raw_payload"),
                )
                cur.execute(
                    "UPDATE execution_attempt SET status = 'ABANDONED', updated_at = now() "
                    "WHERE id = %s",
                    (attempt_id,),
                )
                audit.record_event(
                    cur, event_type="recovery.reconciled", actor="recovery", action_id=action_id,
                    payload={"external_result_id": ext["external_result_id"]},
                )
                return {"outcome": "reconciled", "external_result_id": ext["external_result_id"]}

        # IDEMPOTENT, or RECONCILABLE with no confirmed external effect -> safe reclaim.
        cur.execute(
            "UPDATE execution_attempt SET status = 'ABANDONED', updated_at = now() WHERE id = %s",
            (attempt_id,),
        )
        handle = execution._create_attempt(cur, action_id, safety_mode, "recovery", lease_seconds)
        if action_status == "RECOVERY_REQUIRED":
            execution._set_status(cur, action_id, "RUNNING", actor="recovery")
        audit.record_event(
            cur, event_type="recovery.reclaimed", actor="recovery", action_id=action_id,
            payload={"attempt": handle.attempt_number, "safety_mode": safety_mode},
        )
        return {"outcome": "reclaimed", "attempt_number": handle.attempt_number}
