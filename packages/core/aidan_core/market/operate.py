"""Operate Runtime — allocator-ready market evidence bundle (Gate 7 Slice 3).

Reconstructs, by pure query over immutable canonical state, the evidence a future allocator
(Gate 8) needs to decide the highest-value next action — WITHOUT making that decision. The
bundle keeps the load-bearing layers distinct:

    market-action Proof Receipt  (the exact authorized action occurred)
    market_observation           (externally-attributable outcome evidence)
    market_interpretation        (bounded, provenance-cited reading — NOT evidence)

It contains NO authoritative CONTINUE / KILL / SCALE / BUILD / next-action recommendation and
writes nothing. Repeated market cycles are represented by the existing ActionRequest / spec
primitives: each market action has its own spec, so its own bundle reconstructs independently
and earlier history is never rewritten. This is a read composition, not a workflow engine.
"""
from __future__ import annotations

from . import metrics as metrics_mod
from .interpretation import interpretations_for
from .observation import observations_for


def market_action_specs_for_venture(conn, venture_id: str) -> list[dict]:
    """Every frozen market action for a venture, oldest first (one per market ActionRequest)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, action_request_id FROM market_action_spec WHERE venture_id = %s "
            "ORDER BY created_at, id", (venture_id,))
        return [{"market_action_spec_id": str(r[0]), "action_request_id": str(r[1])}
                for r in cur.fetchall()]


def _action_proof(conn, action_request_id: str):
    """The VERIFIED market-action Proof Receipt identity, or None. Kept SEPARATE from evidence."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, result, verification_type, execution_attempt_id FROM proof_receipt "
            "WHERE action_request_id = %s AND verification_type = 'MARKET_ACTION' AND result = 'VERIFIED'",
            (action_request_id,))
        r = cur.fetchone()
        if r is None:
            return None
        return {"proof_receipt_id": str(r[0]), "result": r[1],
                "verification_type": r[2], "execution_attempt_id": str(r[3])}


def _observations(conn, market_action_spec_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, observation_type, external_event_id, source_instance_ref, occurred_at, "
            "evidence_hash FROM market_observation WHERE market_action_spec_id = %s "
            "ORDER BY created_at, id", (market_action_spec_id,))
        return [{"id": str(r[0]), "observation_type": r[1], "external_event_id": r[2],
                 "source_instance_ref": r[3], "occurred_at": r[4], "evidence_hash": r[5]}
                for r in cur.fetchall()]


def market_evidence_bundle(conn, market_action_spec_id: str) -> dict:
    """Assemble the allocator-ready evidence bundle for one market action.

    Includes the frozen action spec + its Gate-2/3 provenance, the action Proof Receipt (kept
    distinct), canonical observations (contradictory evidence retained as-is), deterministic
    derived counts, and interpretations (kept distinct, with their exact source provenance). It
    contains NO investment decision and NO next-action recommendation.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, action_request_id, opportunity_id, validation_test_id, channel_kind, "
            "content_hash, authorized_spend_amount, spend_currency, action_spec_hash "
            "FROM market_action_spec WHERE id = %s", (market_action_spec_id,))
        s = cur.fetchone()
    if s is None:
        from ..errors import NotFoundError
        raise NotFoundError(f"market_action_spec {market_action_spec_id} does not exist")

    spec = {
        "market_action_spec_id": str(s[0]), "action_request_id": str(s[2]),
        "channel_kind": s[5], "content_hash": s[6],
        "authorized_spend_amount": s[7], "spend_currency": s[8], "action_spec_hash": s[9],
    }
    provenance = {"opportunity_id": str(s[3]), "validation_test_id": str(s[4])}
    return {
        "venture_id": str(s[1]),
        "market_action_spec": spec,
        "provenance": provenance,
        "action_proof": _action_proof(conn, s[2]),          # exact action occurred (distinct)
        "observations": _observations(conn, market_action_spec_id),  # evidence (distinct)
        "counts": metrics_mod.market_metrics(conn, market_action_spec_id),
        "interpretations": interpretations_for(conn, market_action_spec_id),  # advisory (distinct)
        # Intentionally absent: any CONTINUE/KILL/SCALE decision or next-action recommendation.
    }
