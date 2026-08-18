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


def _write_receipt(
    cur,
    action_request_id: str,
    execution_result_id: str,
    *,
    verdict: str,
    verification_type: str,
    verifier_name: str,
    evidence_hash: str,
    execution_attempt_id=None,
):
    """The ONLY statement that writes a proof receipt (cursor-based).

    Internal/trusted: consumes an ALREADY-DERIVED deterministic verification
    outcome. Callers must have produced the verdict by running a deterministic
    verifier (the built-in token verifier, or a Gate 4 spec-resolved verifier);
    there is no public API through which an untrusted caller reaches this. Returns
    the proof id.
    """
    cur.execute(
        """
        INSERT INTO proof_receipt
            (action_request_id, execution_result_id, execution_attempt_id,
             verification_type, verifier, result, evidence_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (action_request_id, execution_result_id, execution_attempt_id,
         verification_type, verifier_name, verdict, evidence_hash),
    )
    return cur.fetchone()[0]


def _record_receipt(
    cur,
    action_request_id: str,
    execution_result_id: str,
    reported_outcome: str,
    raw_payload,
    *,
    execution_attempt_id=None,
) -> tuple:
    """Verify with the built-in deterministic token verifier and persist the receipt.

    Returns ``(verdict, proof_id)``. The verdict is ALWAYS derived here; there is no
    parameter through which a caller can supply ``VERIFIED``.
    """
    verdict, verification_type, evidence_hash = deterministic_verify(
        action_request_id, reported_outcome, raw_payload
    )
    proof_id = _write_receipt(
        cur, action_request_id, execution_result_id, verdict=verdict,
        verification_type=verification_type, verifier_name=VERIFIER,
        evidence_hash=evidence_hash, execution_attempt_id=execution_attempt_id,
    )
    return verdict, proof_id
