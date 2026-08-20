"""Durable REAL vs SIMULATED evidence origin (Gate 8 Slice 4).

Binds the exact VERIFIED ``MARKET_ACTION`` proof of a consequential market action to a trusted
evidence origin. The ``origin_kind`` is NEVER a caller argument: it is derived INSIDE this writer
from the actual transport type — ``REAL_PROVIDER`` requires a genuine ``PostmarkHttpTransport``
(whose verification path makes real provider calls), and every other transport (including the
fixture ``FakePostmarkTransport`` and the Gate-7 local channel) is ``SIMULATED``. The action must
additionally have been verified by the Postmark verifier, so a REAL origin can never attach to a
local/generic action. Idempotency CONFLICTS on any material provenance change, so a proof already
bound SIMULATED can never be re-bound REAL. ``action_reality`` derives REAL/SIMULATED for an
action from this durable state.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, MarketAuthorityError

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


def _derive_origin_kind(transport) -> str:
    """REAL_PROVIDER only for a genuine production Postmark transport; else SIMULATED. Derived
    from the actual type, never a caller flag — a fixture declaring ``origin_kind=REAL_PROVIDER``
    is not a ``PostmarkHttpTransport`` and stays SIMULATED."""
    from .postmark import PostmarkHttpTransport  # lazy import to avoid a cycle
    return REAL_PROVIDER if isinstance(transport, PostmarkHttpTransport) else SIMULATED


def record_evidence_origin(conn, action_request_id: str, *, transport, provider_kind: str,
                           source_instance_ref: str, actor: str = "market") -> OriginResult:
    """Bind the action's VERIFIED MARKET_ACTION proof to a trusted origin derived from the
    transport. Only a Postmark-verified action may be bound (a REAL origin cannot attach to a
    local/generic action). Idempotent per proof; conflicting provenance is rejected."""
    from .postmark import POSTMARK_VERIFIER_KIND  # lazy import to avoid a cycle
    origin_kind = _derive_origin_kind(transport)
    with db.transaction(conn) as cur:
        cur.execute("SELECT verifier_kind FROM execution_spec WHERE action_request_id = %s", (action_request_id,))
        vk = cur.fetchone()
        if vk is None or vk[0] != POSTMARK_VERIFIER_KIND:
            raise MarketAuthorityError(
                "evidence origin can only be bound to a Postmark-verified market action")
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
        cur.execute("SELECT id, origin_kind, origin_hash FROM external_evidence_origin WHERE proof_receipt_id = %s",
                    (proof_id,))
        existing = cur.fetchone()
        if existing is not None:
            if existing[2] != origin_hash:
                raise IdempotencyConflictError(
                    f"proof {proof_id} already has a different evidence origin ({existing[1]}); "
                    "origin cannot be re-bound")
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


def has_real_no_response(conn, market_action_spec_id: str, action_request_id: str) -> bool:
    """True iff a deterministic NO_RESPONSE completion exists for the action AND the action's
    proof is REAL_PROVIDER."""
    if action_reality(conn, action_request_id) != REAL:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM market_window_completion WHERE market_action_spec_id = %s "
                    "AND completion_type = 'NO_RESPONSE' LIMIT 1", (market_action_spec_id,))
        return cur.fetchone() is not None


def record_observation_origin(conn, market_observation_id: str, *, transport, provider_kind: str,
                              source_instance_ref: str, provider_event_ref: str, actor: str = "market"):
    """Bind a market_observation to trusted REAL_PROVIDER provenance — ONLY from the trusted
    Postmark ingestion path. REAL_PROVIDER requires BOTH a genuine PostmarkHttpTransport AND that
    the observation's action already carries a REAL_PROVIDER action proof (fail-closed: otherwise
    no row is written and the observation is SIMULATED by absence). Never a caller flag. Idempotent
    per observation; conflicting provenance is rejected."""
    origin_kind = _derive_origin_kind(transport)
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id, market_action_spec_id, action_request_id FROM market_observation "
                    "WHERE id = %s", (market_observation_id,))
        obs = cur.fetchone()
        if obs is None:
            raise NotFoundError(f"market_observation {market_observation_id} does not exist")
        venture_id, spec_id, action_request_id = obs
        proof = _verified_proof(cur, action_request_id)
        # a REAL observation must be attributable to the exact action's REAL_PROVIDER action proof
        if origin_kind == REAL_PROVIDER:
            cur.execute("SELECT eo.origin_kind FROM external_evidence_origin eo "
                        "JOIN proof_receipt pr ON pr.id = eo.proof_receipt_id WHERE pr.action_request_id = %s "
                        "AND pr.verification_type = 'MARKET_ACTION' AND pr.result = 'VERIFIED'", (action_request_id,))
            arow = cur.fetchone()
            if proof is None or arow is None or arow[0] != REAL_PROVIDER:
                origin_kind = SIMULATED   # fail-closed
        if origin_kind != REAL_PROVIDER:
            return None                    # absence of a REAL row == SIMULATED
        proof_id = proof[0]
        origin_hash = canonical_payload_hash({
            "venture_id": str(venture_id), "market_observation_id": str(market_observation_id),
            "market_action_spec_id": str(spec_id), "proof_receipt_id": str(proof_id),
            "origin_kind": origin_kind, "provider_kind": provider_kind,
            "source_instance_ref": source_instance_ref, "provider_event_ref": provider_event_ref})
        cur.execute("SELECT id, origin_hash FROM market_observation_origin WHERE market_observation_id = %s",
                    (market_observation_id,))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != origin_hash:
                raise IdempotencyConflictError(
                    f"observation {market_observation_id} already has a different origin; cannot re-bind")
            return str(existing[0])
        cur.execute(
            "INSERT INTO market_observation_origin (venture_id, market_observation_id, market_action_spec_id, "
            "proof_receipt_id, origin_kind, provider_kind, source_instance_ref, provider_event_ref, origin_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (venture_id, market_observation_id, spec_id, proof_id, origin_kind, provider_kind,
             source_instance_ref, provider_event_ref, origin_hash))
        oid = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="market.observation_origin_bound", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"market_observation_origin_id": str(oid), "origin_kind": origin_kind,
                     "market_observation_id": str(market_observation_id)})
    return str(oid)


def observation_is_real(conn, market_observation_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM market_observation_origin WHERE market_observation_id = %s "
                    "AND origin_kind = %s LIMIT 1", (market_observation_id, REAL_PROVIDER))
        return cur.fetchone() is not None
