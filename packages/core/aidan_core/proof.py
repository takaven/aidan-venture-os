"""Deterministic Proof Receipts.

A raw executor result is not proof. Canonical success requires a VERIFIED
proof produced by the deterministic verifier here — a caller cannot simply
persist ``result='VERIFIED'``; the only path that writes a receipt runs the
verifier. No LLM verifier. At most one VERIFIED receipt exists per action
(enforced by a partial unique index).
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

from .actions import canonical_payload_hash

VERIFICATION_TYPE = "token-match-v1"
VERIFIER = "gate1.deterministic"


def expected_token(action_request_id: str) -> str:
    """Deterministic proof token an executor must echo for a valid result."""
    return f"proof:{action_request_id}"


def deterministic_verify(
    action_request_id: str, reported_outcome: str, raw_payload: Optional[dict[str, Any]]
) -> Tuple[str, str, str]:
    """Verify a raw result deterministically.

    Returns ``(result, verification_type, evidence_hash)`` where result is
    'VERIFIED' or 'FAILED'. A mere reported "success" is insufficient: the raw
    payload must carry the exact expected proof token.
    """
    payload = raw_payload or {}
    ok = reported_outcome == "success" and payload.get("token") == expected_token(action_request_id)
    result = "VERIFIED" if ok else "FAILED"
    return result, VERIFICATION_TYPE, canonical_payload_hash(payload)


def verified_proof_id(cur, action_request_id: str):
    """Return the id of the existing VERIFIED proof for an action, or None."""
    cur.execute(
        "SELECT id FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'",
        (action_request_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def insert_receipt(
    cur,
    action_request_id: str,
    execution_result_id: str,
    result: str,
    verification_type: str,
    evidence_hash: str,
) -> str:
    """Insert a proof receipt (cursor-based). Callers must pass a verifier result."""
    cur.execute(
        """
        INSERT INTO proof_receipt
            (action_request_id, execution_result_id, verification_type, verifier, result, evidence_hash)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (action_request_id, execution_result_id, verification_type, VERIFIER, result, evidence_hash),
    )
    return cur.fetchone()[0]
