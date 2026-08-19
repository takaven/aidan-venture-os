"""Provenance-bound market interpretation (Gate 7 Slice 3).

An interpretation is a bounded reading of one or more canonical ``market_observation`` rows.
It is NOT evidence and carries NO decision authority: creating one appends interpretation +
exact source-provenance rows and NOTHING else — it never writes an investment decision,
transitions lifecycle, moves capital, creates an ActionRequest, mutates a validation_result,
or rewrites an observation / Proof Receipt. An interpreter (deterministic kernel, model, or
adapter) is advisory: any ``recommended_lifecycle`` / ``SCALE`` / ``verified`` field in its
payload is inert DATA.

New evidence yields a NEW interpretation (a new ``interpretation_key``); an existing key must
converge on identical content and conflicts on materially changed content or a changed source
set. Every interpretation must cite >= 1 observation; all cited observations must belong to
the same venture and (action-scoped) to the same market action, else the write is rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import (
    IdempotencyConflictError,
    InconsistentCanonicalStateError,
    MarketAuthorityError,
    NotFoundError,
)

INTERPRETATION_TYPES = frozenset({"MARKET_SUMMARY", "RESPONSE_PATTERN", "COMMERCIAL_SIGNAL"})


@dataclass(frozen=True)
class InterpretationResult:
    market_interpretation_id: str
    interpretation_hash: str
    source_observation_ids: tuple[str, ...]
    created: bool


def _interpretation_hash(*, venture_id, market_action_spec_id, interpreter_kind, interpreter_ref,
                         interpretation_type, sources, payload) -> str:
    """Deterministic identity over provenance + payload.

    ``sources`` is the canonical (sorted, de-duplicated) list of ``(observation_id,
    evidence_hash)`` pairs — binding the interpretation to the EXACT immutable observations
    (and their content) so that a changed source set changes the hash. Source ORDER never
    affects the hash. The caller-supplied ``interpretation_key`` is the stable identity and is
    intentionally NOT hashed here.
    """
    return canonical_payload_hash({
        "venture_id": str(venture_id),
        "market_action_spec_id": str(market_action_spec_id),
        "interpreter_kind": interpreter_kind,
        "interpreter_ref": interpreter_ref,
        "interpretation_type": interpretation_type,
        "sources": [[str(oid), eh] for oid, eh in sources],
        "payload": payload,
    })


def create_market_interpretation(
    conn,
    market_action_spec_id: str,
    *,
    interpretation_key: str,
    interpreter_kind: str,
    interpretation_type: str,
    interpretation_payload: Optional[dict[str, Any]] = None,
    source_observation_ids: list[str],
    interpreter_ref: Optional[str] = None,
    actor: str = "market",
) -> InterpretationResult:
    """Append a provenance-bound interpretation of the cited market observations.

    Idempotent per ``(venture, interpretation_key)``: identical content converges; materially
    changed payload or a changed source set conflicts. Creates NO decision/lifecycle/capital/
    ActionRequest/validation state and mutates no observation.
    """
    if interpretation_type not in INTERPRETATION_TYPES:
        raise ValueError(
            f"unknown interpretation_type {interpretation_type!r}; allowed: {sorted(INTERPRETATION_TYPES)}")
    if not interpretation_key or not str(interpretation_key).strip():
        raise ValueError("interpretation_key is required")
    if not interpreter_kind or not str(interpreter_kind).strip():
        raise ValueError("interpreter_kind is required")
    if not source_observation_ids:
        raise MarketAuthorityError("an interpretation must cite at least one market observation")
    payload = interpretation_payload or {}
    # Canonicalize the source set: de-duplicate, keep as strings; order is normalized at hash time.
    unique_ids = list(dict.fromkeys(str(o) for o in source_observation_ids))

    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT venture_id, action_request_id FROM market_action_spec WHERE id = %s",
            (market_action_spec_id,))
        ms = cur.fetchone()
        if ms is None:
            raise NotFoundError(f"market_action_spec {market_action_spec_id} does not exist")
        venture_id, action_request_id = ms

        # Resolve each cited observation and enforce single-venture + action-scoped provenance.
        sources: list[tuple[str, str]] = []
        for oid in unique_ids:
            cur.execute(
                "SELECT venture_id, market_action_spec_id, evidence_hash FROM market_observation "
                "WHERE id = %s", (oid,))
            orow = cur.fetchone()
            if orow is None:
                raise NotFoundError(f"market_observation {oid} does not exist")
            o_venture, o_spec, o_hash = orow
            if str(o_venture) != str(venture_id):
                raise InconsistentCanonicalStateError(
                    "cited observation belongs to a different venture")
            if str(o_spec) != str(market_action_spec_id):
                raise MarketAuthorityError(
                    "cited observation belongs to a different market action")
            sources.append((str(oid), o_hash))
        sources.sort(key=lambda s: s[0])

        interpretation_hash = _interpretation_hash(
            venture_id=venture_id, market_action_spec_id=market_action_spec_id,
            interpreter_kind=interpreter_kind, interpreter_ref=interpreter_ref,
            interpretation_type=interpretation_type, sources=sources, payload=payload)

        cur.execute(
            "SELECT id, interpretation_hash FROM market_interpretation "
            "WHERE venture_id = %s AND interpretation_key = %s",
            (venture_id, interpretation_key))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != interpretation_hash:
                raise IdempotencyConflictError(
                    f"interpretation {interpretation_key!r} already exists with materially "
                    "different content or a different source set")
            return InterpretationResult(
                existing[0], interpretation_hash, tuple(s[0] for s in sources), created=False)

        cur.execute(
            """
            INSERT INTO market_interpretation
                (venture_id, market_action_spec_id, interpretation_key, interpreter_kind,
                 interpreter_ref, interpretation_type, interpretation_payload, interpretation_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, market_action_spec_id, interpretation_key, interpreter_kind,
             interpreter_ref, interpretation_type, Json(payload), interpretation_hash))
        interp_id = cur.fetchone()[0]
        for oid, _hash in sources:
            cur.execute(
                "INSERT INTO market_interpretation_source (interpretation_id, observation_id, venture_id) "
                "VALUES (%s, %s, %s)", (interp_id, oid, venture_id))
        audit.record_event(
            cur, event_type="market.interpretation_created", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"market_interpretation_id": str(interp_id), "interpretation_type": interpretation_type,
                     "source_observation_ids": [s[0] for s in sources]})
    return InterpretationResult(
        interp_id, interpretation_hash, tuple(s[0] for s in sources), created=True)


def interpretations_for(conn, market_action_spec_id: str):
    """All interpretations for a market action with their cited source observation ids."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, interpretation_key, interpreter_kind, interpreter_ref, interpretation_type, "
            "interpretation_hash, created_at FROM market_interpretation "
            "WHERE market_action_spec_id = %s ORDER BY created_at, id", (market_action_spec_id,))
        rows = cur.fetchall()
        out = []
        for r in rows:
            cur.execute(
                "SELECT observation_id FROM market_interpretation_source "
                "WHERE interpretation_id = %s ORDER BY observation_id", (r[0],))
            src = [str(x[0]) for x in cur.fetchall()]
            out.append({
                "id": str(r[0]), "interpretation_key": r[1], "interpreter_kind": r[2],
                "interpreter_ref": r[3], "interpretation_type": r[4], "interpretation_hash": r[5],
                "created_at": r[6], "source_observation_ids": src})
        return out
