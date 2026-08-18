"""Canonical Claims and their SUPPORTS/CONTRADICTS relations to Observations.

A Claim is a proposition, never automatically true. Its support state is
DERIVED structurally from append-only relations — it is never caller-set.
Contradictory evidence coexists and is never overwritten: later evidence changes
the derived state without mutating any prior relation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import EvidenceRelationConflictError, IdempotencyConflictError, NotFoundError

_STANCES = {"SUPPORTS", "CONTRADICTS"}


@dataclass(frozen=True)
class ClaimResult:
    evidence_record_id: str
    created: bool


@dataclass(frozen=True)
class LinkResult:
    relation_id: str
    created: bool


def create_claim(
    conn, venture_id: str, *, statement: str, claim_key: str, actor: str = "research"
) -> ClaimResult:
    """Create a Claim proposition. Idempotent per (venture, claim_key)."""
    if not statement:
        raise ValueError("statement is required")
    if not claim_key:
        raise ValueError("claim_key is required")
    digest = canonical_payload_hash({"statement": statement})

    with db.transaction(conn) as cur:
        cur.execute(
            """
            SELECT c.evidence_record_id, er.content_hash
            FROM claim c JOIN evidence_record er ON er.id = c.evidence_record_id
            WHERE c.venture_id = %s AND c.claim_key = %s
            """,
            (venture_id, claim_key),
        )
        row = cur.fetchone()
        if row is not None:
            existing_id, existing_hash = row
            if existing_hash != digest:
                raise IdempotencyConflictError(
                    f"claim key {claim_key!r} reused with a different statement"
                )
            return ClaimResult(existing_id, created=False)

        cur.execute(
            "INSERT INTO evidence_record (venture_id, kind, content_hash) "
            "VALUES (%s, 'CLAIM', %s) RETURNING id",
            (venture_id, digest),
        )
        er_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO claim (evidence_record_id, venture_id, statement, claim_key) "
            "VALUES (%s, %s, %s, %s)",
            (er_id, venture_id, statement, claim_key),
        )
        audit.record_event(
            cur, event_type="evidence.claim_created", actor=actor, venture_id=venture_id,
            payload={"evidence_record_id": str(er_id)},
        )
    return ClaimResult(er_id, created=True)


def link_evidence(
    conn,
    *,
    claim_id: str,
    observation_id: str,
    stance: str,
    reason: Optional[str] = None,
    actor: str = "research",
) -> LinkResult:
    """Assert a SUPPORTS/CONTRADICTS relation from an Observation to a Claim.

    Idempotent for an exact (claim, observation, stance) retry. The same pair
    re-asserted with the opposite stance is a deterministic conflict — the first
    is never silently replaced.
    """
    if stance not in _STANCES:
        raise ValueError(f"stance must be one of {sorted(_STANCES)}")

    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM claim WHERE evidence_record_id = %s", (claim_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"claim {claim_id} does not exist")
        venture_id = row[0]

        # The observation-side FK enforces that observation_id is an OBSERVATION of
        # the same venture (cross-venture / non-observation -> ForeignKeyViolation).
        cur.execute(
            """
            INSERT INTO claim_evidence (claim_id, observation_id, venture_id, stance, reason)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (claim_id, observation_id) DO NOTHING
            RETURNING id
            """,
            (claim_id, observation_id, venture_id, stance, reason),
        )
        row = cur.fetchone()
        if row is not None:
            relation_id = row[0]
            event = "evidence.claim_supported" if stance == "SUPPORTS" else "evidence.claim_contradicted"
            audit.record_event(
                cur, event_type=event, actor=actor, venture_id=venture_id,
                payload={"claim_id": str(claim_id), "observation_id": str(observation_id), "stance": stance},
            )
            return LinkResult(relation_id, created=True)

        cur.execute(
            "SELECT id, stance FROM claim_evidence WHERE claim_id = %s AND observation_id = %s",
            (claim_id, observation_id),
        )
        existing_id, existing_stance = cur.fetchone()
        if existing_stance != stance:
            raise EvidenceRelationConflictError(
                f"pair already asserted as {existing_stance}; cannot re-assert as {stance}"
            )
        return LinkResult(existing_id, created=False)


def claim_state(conn, claim_id: str) -> str:
    """Derive claim state structurally from relations. Never persisted."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE stance = 'SUPPORTS'), "
            "       count(*) FILTER (WHERE stance = 'CONTRADICTS') "
            "FROM claim_evidence WHERE claim_id = %s",
            (claim_id,),
        )
        supports, contradicts = cur.fetchone()
    if supports == 0 and contradicts == 0:
        return "UNSUPPORTED"
    if supports > 0 and contradicts == 0:
        return "SUPPORTED"
    if supports == 0 and contradicts > 0:
        return "CONTRADICTED"
    return "DISPUTED"


def provenance(conn, claim_id: str) -> dict:
    """Structured traversal Claim -> Observation -> Source Receipt. No prose, no LLM."""
    with conn.cursor() as cur:
        cur.execute("SELECT statement FROM claim WHERE evidence_record_id = %s", (claim_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"claim {claim_id} does not exist")
        statement = row[0]
        cur.execute(
            """
            SELECT ce.stance, o.evidence_record_id, o.statement, o.source_evidence_id, o.source_locator,
                   sr.locator, sr.retrieved_at, sr.published_at, sr.publication_time_known,
                   sr.reliability_code, ser.content_hash
            FROM claim_evidence ce
            JOIN observation o ON o.evidence_record_id = ce.observation_id
            JOIN source_receipt sr ON sr.evidence_record_id = o.source_evidence_id
            JOIN evidence_record ser ON ser.id = sr.evidence_record_id
            WHERE ce.claim_id = %s
            ORDER BY ce.created_at, ce.id
            """,
            (claim_id,),
        )
        paths = [
            {
                "stance": r[0],
                "observation_id": r[1],
                "observation_statement": r[2],
                "source_evidence_id": r[3],
                "observation_source_locator": r[4],
                "source_locator": r[5],
                "retrieved_at": r[6],
                "published_at": r[7],
                "publication_time_known": r[8],
                "reliability_code": r[9],
                "source_content_hash": r[10],
            }
            for r in cur.fetchall()
        ]
    return {"claim_id": claim_id, "statement": statement, "state": claim_state(conn, claim_id), "paths": paths}
