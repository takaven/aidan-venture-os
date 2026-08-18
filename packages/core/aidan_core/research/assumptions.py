"""Assumptions — explicit propositions held under uncertainty. NOT evidence.

Confidence and importance are categorical (no decimals/percentages). Every
assumption records its consequence-if-false and a cheapest credible test, which
is a plain research hypothesis only — Gate 3 selects/prices/funds experiments.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError

IMPORTANCE = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}


@dataclass(frozen=True)
class AssumptionResult:
    assumption_id: str
    created: bool


def create_assumption(
    conn, venture_id: str, *, proposition: str, assumption_key: str, importance: str,
    confidence: str, consequence_if_false: str, cheapest_test: str, actor: str = "research",
) -> AssumptionResult:
    if not proposition:
        raise ValueError("proposition is required")
    if not assumption_key:
        raise ValueError("assumption_key is required")
    if importance not in IMPORTANCE:
        raise ValueError(f"importance must be one of {sorted(IMPORTANCE)}")
    if confidence not in CONFIDENCE:
        raise ValueError(f"confidence must be one of {sorted(CONFIDENCE)} (categorical, not numeric)")
    if not consequence_if_false:
        raise ValueError("consequence_if_false is required")
    if not cheapest_test:
        raise ValueError("cheapest_test is required")
    digest = canonical_payload_hash({"proposition": proposition})
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT id, proposition_hash FROM assumption WHERE venture_id = %s AND assumption_key = %s",
            (venture_id, assumption_key),
        )
        row = cur.fetchone()
        if row is not None:
            if row[1] != digest:
                raise IdempotencyConflictError(
                    f"assumption key {assumption_key!r} reused with a different proposition"
                )
            return AssumptionResult(row[0], created=False)
        cur.execute(
            """
            INSERT INTO assumption
                (venture_id, assumption_key, proposition, proposition_hash, importance, confidence,
                 consequence_if_false, cheapest_test)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, assumption_key, proposition, digest, importance, confidence,
             consequence_if_false, cheapest_test),
        )
        assumption_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="research.assumption_created", actor=actor, venture_id=venture_id,
            payload={"assumption_id": str(assumption_id), "importance": importance, "confidence": confidence},
        )
    return AssumptionResult(assumption_id, created=True)


def _link(conn, table, col, assumption_id, other_id, event, actor):
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM assumption WHERE id = %s", (assumption_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"assumption {assumption_id} does not exist")
        venture_id = row[0]
        cur.execute(
            f"INSERT INTO {table} (assumption_id, {col}, venture_id) VALUES (%s, %s, %s) "
            f"ON CONFLICT (assumption_id, {col}) DO NOTHING RETURNING id",
            (assumption_id, other_id, venture_id),
        )
        if cur.fetchone() is None:
            return False
        audit.record_event(
            cur, event_type=event, actor=actor, venture_id=venture_id,
            payload={"assumption_id": str(assumption_id), col: str(other_id)},
        )
    return True


def link_claim(conn, *, assumption_id: str, claim_id: str, actor: str = "research") -> bool:
    return _link(conn, "assumption_claim", "claim_id", assumption_id, claim_id,
                 "research.assumption_claim_linked", actor)


def link_interpretation(conn, *, assumption_id: str, interpretation_id: str, actor: str = "research") -> bool:
    return _link(conn, "assumption_interpretation", "interpretation_id", assumption_id, interpretation_id,
                 "research.assumption_interpretation_linked", actor)
