"""Provenance-linked Observations (append-only OBSERVATION evidence subtype).

An Observation is directly observed source material bound 1:1 to its OBSERVATION
``evidence_record`` envelope and to an exact Source Receipt. It carries no
interpretation, assumption, recommendation or decision, and no confidence score.

Provenance here is *link integrity*, not semantic truth: Slice 1 retains only a
bounded excerpt + content hash, so textual occurrence of the observed statement
in the full source is NOT deterministically verified (see ADR-008).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError


@dataclass(frozen=True)
class ObservationResult:
    evidence_record_id: str
    created: bool


def _observation_hash(statement: str, source_evidence_id, source_locator: Optional[str]) -> str:
    return canonical_payload_hash(
        {"statement": statement, "source": str(source_evidence_id), "locator": source_locator or ""}
    )


def create_observation(
    conn,
    venture_id: str,
    *,
    source_evidence_id: str,
    statement: str,
    observation_key: str,
    source_locator: Optional[str] = None,
    excerpt: Optional[str] = None,
    actor: str = "research",
) -> ObservationResult:
    """Create an Observation bound to an exact Source Receipt. Idempotent per key."""
    if not statement:
        raise ValueError("statement is required")
    if not observation_key:
        raise ValueError("observation_key is required")
    digest = _observation_hash(statement, source_evidence_id, source_locator)

    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT 1 FROM source_receipt WHERE evidence_record_id = %s AND venture_id = %s",
            (source_evidence_id, venture_id),
        )
        if cur.fetchone() is None:
            raise NotFoundError(
                f"source receipt {source_evidence_id} not found for this venture"
            )

        cur.execute(
            """
            SELECT o.evidence_record_id, er.content_hash
            FROM observation o JOIN evidence_record er ON er.id = o.evidence_record_id
            WHERE o.venture_id = %s AND o.observation_key = %s
            """,
            (venture_id, observation_key),
        )
        row = cur.fetchone()
        if row is not None:
            existing_id, existing_hash = row
            if existing_hash != digest:
                raise IdempotencyConflictError(
                    f"observation key {observation_key!r} reused with a different observation"
                )
            return ObservationResult(existing_id, created=False)

        cur.execute(
            "INSERT INTO evidence_record (venture_id, kind, content_hash) "
            "VALUES (%s, 'OBSERVATION', %s) RETURNING id",
            (venture_id, digest),
        )
        er_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO observation
                (evidence_record_id, venture_id, source_evidence_id, source_locator, statement,
                 excerpt, observation_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (er_id, venture_id, source_evidence_id, source_locator, statement, excerpt, observation_key),
        )
        audit.record_event(
            cur, event_type="evidence.observation_created", actor=actor, venture_id=venture_id,
            payload={"evidence_record_id": str(er_id), "source_evidence_id": str(source_evidence_id)},
        )
    return ObservationResult(er_id, created=True)


def get_observation(conn, evidence_record_id: str):
    """Return the observation joined to its source receipt and envelope hash."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.evidence_record_id, o.venture_id, o.source_evidence_id, o.source_locator,
                   o.statement, o.excerpt, o.observation_key, er.content_hash, er.kind
            FROM observation o JOIN evidence_record er ON er.id = o.evidence_record_id
            WHERE o.evidence_record_id = %s
            """,
            (evidence_record_id,),
        )
        return cur.fetchone()
