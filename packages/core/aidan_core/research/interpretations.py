"""Interpretations — attributed reasoning over Claims. NOT evidence.

An Interpretation reasons over one or more Claims but never mutates their
structural state and can never be persisted as an Observation/Claim. ``produced_by``
is provenance about who/what reasoned, not proof of correctness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError


@dataclass(frozen=True)
class InterpretationResult:
    interpretation_id: str
    created: bool


def create_interpretation(
    conn, venture_id: str, *, statement: str, interpretation_key: str, produced_by: str,
    actor: str = "research",
) -> InterpretationResult:
    if not statement:
        raise ValueError("statement is required")
    if not interpretation_key:
        raise ValueError("interpretation_key is required")
    if not produced_by:
        raise ValueError("produced_by is required")
    digest = canonical_payload_hash({"statement": statement})
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT id, statement_hash FROM interpretation WHERE venture_id = %s AND interpretation_key = %s",
            (venture_id, interpretation_key),
        )
        row = cur.fetchone()
        if row is not None:
            if row[1] != digest:
                raise IdempotencyConflictError(
                    f"interpretation key {interpretation_key!r} reused with a different statement"
                )
            return InterpretationResult(row[0], created=False)
        cur.execute(
            "INSERT INTO interpretation (venture_id, interpretation_key, statement, statement_hash, produced_by) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (venture_id, interpretation_key, statement, digest, produced_by),
        )
        interpretation_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="research.interpretation_created", actor=actor, venture_id=venture_id,
            payload={"interpretation_id": str(interpretation_id)},
        )
    return InterpretationResult(interpretation_id, created=True)


def link_claim(conn, *, interpretation_id: str, claim_id: str, actor: str = "research") -> bool:
    """Link an Interpretation to a Claim. Idempotent; cross-venture rejected (FK)."""
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM interpretation WHERE id = %s", (interpretation_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"interpretation {interpretation_id} does not exist")
        venture_id = row[0]
        cur.execute(
            "INSERT INTO interpretation_claim (interpretation_id, claim_id, venture_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (interpretation_id, claim_id) DO NOTHING RETURNING id",
            (interpretation_id, claim_id, venture_id),
        )
        if cur.fetchone() is None:
            return False
        audit.record_event(
            cur, event_type="research.interpretation_claim_linked", actor=actor, venture_id=venture_id,
            payload={"interpretation_id": str(interpretation_id), "claim_id": str(claim_id)},
        )
    return True
