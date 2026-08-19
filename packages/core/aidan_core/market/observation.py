"""Externally-attributable market observation evidence (Gate 7 Slice 2).

Records an externally-observed market outcome as immutable evidence, SEPARATE from the
market-action Proof Receipt and from interpretation. The event payload is UNTRUSTED data:
it has no authority to execute commands, change Policy/lifecycle, write an investment
decision, or trigger a new action — this function only appends evidence.

Observations may arrive asynchronously (after the action completed) and are still
recordable after a kill switch is engaged (a kill blocks NEW consequential actions; it
never erases or suppresses historical external evidence). ``NO_RESPONSE`` is not an
external event and is rejected here — absence is a Slice-3 derivation from a frozen window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, MarketAuthorityError, NotFoundError
from . import channels as channels_mod

OBSERVATION_TYPES = frozenset(
    {"DELIVERED", "BOUNCED", "OPENED", "CLICKED", "REPLIED", "UNSUBSCRIBE"})


@dataclass(frozen=True)
class ObservationResult:
    market_observation_id: str
    evidence_hash: str
    created: bool


def _evidence_hash(*, venture_id, market_action_spec_id, action_request_id, channel_kind,
                   source_instance_ref, external_event_id, observation_type, occurred_at,
                   source_action_ref, raw_evidence) -> str:
    return canonical_payload_hash({
        "venture_id": str(venture_id),
        "market_action_spec_id": str(market_action_spec_id),
        "action_request_id": str(action_request_id),
        "channel_kind": channel_kind,
        "source_instance_ref": source_instance_ref,
        "external_event_id": external_event_id,
        "observation_type": observation_type,
        "occurred_at": None if occurred_at is None else str(occurred_at),
        "source_action_ref": source_action_ref,
        "raw_evidence": raw_evidence,
    })


def record_market_observation(
    conn,
    market_action_spec_id: str,
    *,
    external_event_id: str,
    observation_type: str,
    channel_kind: str,
    source_instance_ref: Optional[str] = None,
    occurred_at=None,
    source_action_ref: Optional[str] = None,
    raw_evidence: Optional[dict[str, Any]] = None,
    execution_attempt_id: Optional[str] = None,
    actor: str = "market",
) -> ObservationResult:
    """Append an external market observation. Idempotent per source-scoped external event.

    Rejects ``NO_RESPONSE`` (not an external event) and any type outside the finite
    vocabulary; binds the observation to the exact venture/action of the referenced
    market_action_spec and verifies channel + source-instance consistency; converges on an
    identical duplicate; conflicts on a materially different payload for the same scoped id.
    """
    if observation_type == "NO_RESPONSE":
        raise MarketAuthorityError("NO_RESPONSE is not an external event; it is derived from absence (Slice 3)")
    if observation_type not in OBSERVATION_TYPES:
        raise ValueError(f"unknown observation_type {observation_type!r}; allowed: {sorted(OBSERVATION_TYPES)}")
    if not external_event_id or not str(external_event_id).strip():
        raise ValueError("external_event_id is required")
    raw_evidence = raw_evidence or {}

    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT venture_id, action_request_id, channel_kind FROM market_action_spec WHERE id = %s",
            (market_action_spec_id,))
        ms = cur.fetchone()
        if ms is None:
            raise NotFoundError(f"market_action_spec {market_action_spec_id} does not exist")
        venture_id, action_request_id, spec_channel = ms
        if str(channel_kind) != str(spec_channel):
            raise MarketAuthorityError("observation channel does not match the market action's channel")
        expected_source = channels_mod.source_instance_ref(venture_id, channel_kind)
        source_instance_ref = source_instance_ref or expected_source
        if str(source_instance_ref) != str(expected_source):
            raise MarketAuthorityError("observation source instance does not match the venture/channel")
        # An attributed acceptance ref must belong to this venture/source (no cross-action).
        if source_action_ref is not None and not str(source_action_ref).startswith(
                channels_mod.acceptance_id(expected_source, "")):
            raise MarketAuthorityError("source_action_ref does not belong to this venture/source instance")

        evidence_hash = _evidence_hash(
            venture_id=venture_id, market_action_spec_id=market_action_spec_id,
            action_request_id=action_request_id, channel_kind=channel_kind,
            source_instance_ref=source_instance_ref, external_event_id=external_event_id,
            observation_type=observation_type, occurred_at=occurred_at,
            source_action_ref=source_action_ref, raw_evidence=raw_evidence)

        cur.execute(
            "SELECT id, evidence_hash FROM market_observation WHERE venture_id = %s AND channel_kind = %s "
            "AND source_instance_ref = %s AND external_event_id = %s",
            (venture_id, channel_kind, source_instance_ref, external_event_id))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != evidence_hash:
                raise IdempotencyConflictError(
                    f"external event {external_event_id!r} already recorded with different content")
            return ObservationResult(existing[0], evidence_hash, created=False)

        cur.execute(
            """
            INSERT INTO market_observation
                (venture_id, market_action_spec_id, action_request_id, execution_attempt_id, channel_kind,
                 source_instance_ref, external_event_id, observation_type, occurred_at, source_action_ref,
                 raw_evidence, evidence_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, market_action_spec_id, action_request_id, execution_attempt_id, channel_kind,
             source_instance_ref, external_event_id, observation_type, occurred_at, source_action_ref,
             Json(raw_evidence), evidence_hash))
        obs_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="market.observation_recorded", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"market_observation_id": str(obs_id), "observation_type": observation_type,
                     "external_event_id": external_event_id})
    return ObservationResult(obs_id, evidence_hash, created=True)


def observations_for(conn, market_action_spec_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT observation_type, external_event_id, source_instance_ref, occurred_at "
            "FROM market_observation WHERE market_action_spec_id = %s ORDER BY created_at, id",
            (market_action_spec_id,))
        return cur.fetchall()
