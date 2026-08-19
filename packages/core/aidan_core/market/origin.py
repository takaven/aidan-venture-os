"""Durable REAL vs SIMULATED evidence origin (Gate 8 Slice 4).

Binds the exact VERIFIED ``MARKET_ACTION`` proof of a consequential market action to a trusted
evidence origin. ``record_evidence_origin`` is called by TRUSTED execution code with an
``origin_kind`` taken from the transport's OWN declaration (``FakePostmarkTransport`` ->
SIMULATED, ``PostmarkHttpTransport`` -> REAL_PROVIDER) — never from a caller flag or worker
output. ``action_reality`` derives REAL/SIMULATED for an action from this durable state; the
absence of a REAL_PROVIDER origin (e.g. the Gate-7 local channel) is SIMULATED.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import MarketAuthorityError

REAL_PROVIDER = "REAL_PROVIDER"
SIMULATED = "SIMULATED"
REAL = "REAL"


@dataclass(frozen=True)
class OriginResult:
    external_evidence_origin_id: str
    origin_kind: str
    created: bool


def _verified_proof(cur, action_request_id):
    cur.execute(
        "SELECT pr.id, pr.execution_attempt_id, ar.venture_id FROM proof_receipt pr "
        "JOIN action_request ar ON ar.id = pr.action_request_id "
        "WHERE pr.action_request_id = %s AND pr.verification_type = 'MARKET_ACTION' AND pr.result = 'VERIFIED' "
        "ORDER BY pr.created_at, pr.id LIMIT 1",
        (action_request_id,))
    return cur.fetchone()


def record_evidence_origin(conn, action_request_id: str, *, origin_kind: str, provider_kind: str,
                           source_instance_ref: str, actor: str = "market") -> OriginResult:
    """Bind the action's VERIFIED MARKET_ACTION proof to a trusted origin. Idempotent per proof;
    the SIMULATED/REAL_PROVIDER distinction is authoritative and cannot be flipped by a caller."""
    if origin_kind not in (REAL_PROVIDER, SIMULATED):
        raise ValueError(f"origin_kind must be one of {REAL_PROVIDER!r}/{SIMULATED!r}")
    with db.transaction(conn) as cur:
        proof = _verified_proof(cur, action_request_id)
        if proof is None:
            raise MarketAuthorityError(
                "no VERIFIED MARKET_ACTION proof for the action; evidence origin cannot be bound")
        proof_id, attempt_id, venture_id = proof
        origin_hash = canonical_payload_hash({
            "venture_id": str(venture_id), "proof_receipt_id": str(proof_id),
            "execution_attempt_id": None if attempt_id is None else str(attempt_id),
            "origin_kind": origin_kind, "provider_kind": provider_kind,
            "source_instance_ref": source_instance_ref})
        cur.execute("SELECT id, origin_kind FROM external_evidence_origin WHERE proof_receipt_id = %s", (proof_id,))
        existing = cur.fetchone()
        if existing is not None:
            return OriginResult(str(existing[0]), existing[1], created=False)
        cur.execute(
            "INSERT INTO external_evidence_origin (venture_id, proof_receipt_id, execution_attempt_id, "
            "origin_kind, provider_kind, source_instance_ref, origin_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (venture_id, proof_id, attempt_id, origin_kind, provider_kind, source_instance_ref, origin_hash))
        oid = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="market.evidence_origin_bound", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"external_evidence_origin_id": str(oid), "origin_kind": origin_kind, "provider_kind": provider_kind})
    return OriginResult(str(oid), origin_kind, created=True)


def action_reality(conn, action_request_id: str) -> str:
    """REAL iff the action's VERIFIED MARKET_ACTION proof carries a REAL_PROVIDER origin; else
    SIMULATED (including the Gate-7 local channel and any fixture-backed proof)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT eo.origin_kind FROM external_evidence_origin eo "
            "JOIN proof_receipt pr ON pr.id = eo.proof_receipt_id "
            "WHERE pr.action_request_id = %s AND pr.verification_type = 'MARKET_ACTION' AND pr.result = 'VERIFIED'",
            (action_request_id,))
        row = cur.fetchone()
    return REAL if (row is not None and row[0] == REAL_PROVIDER) else SIMULATED
