"""Adversarial Kill Cases — reasoning that argues against an opportunity.

A Kill Case is reasoning, not evidence. Its dimensions carry categorical
assessments (no scores) and may link to canonical Claims where factual. Missing
evidence is recorded as INSUFFICIENT_EVIDENCE at the dimension level — absence of
evidence is never LOW_RISK. One Kill Case per opportunity; append-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError

REQUIRED_DIMENSIONS = frozenset(
    {
        "DOMINANT_ALTERNATIVES", "WTP_WEAKNESS", "DISTRIBUTION_DIFFICULTY", "COMMODITISATION",
        "SWITCHING_BARRIERS", "REGULATION", "DATA_CONSTRAINTS", "UNIT_ECONOMICS",
        "SUPPORT_BURDEN", "MARKET_SIZE", "TECHNOLOGY_RISK",
    }
)
DISPOSITIONS = {"PROCEED_WITH_RISKS", "KILL", "INSUFFICIENT_EVIDENCE"}
ASSESSMENTS = {"LOW_RISK", "MATERIAL_RISK", "SEVERE_RISK", "INSUFFICIENT_EVIDENCE"}


@dataclass(frozen=True)
class KillCaseResult:
    kill_case_id: str
    created: bool


@dataclass(frozen=True)
class DimensionResult:
    dimension_id: str
    created: bool


def create_kill_case(
    conn, *, opportunity_id: str, kill_case_key: str, disposition: str, actor: str = "research"
) -> KillCaseResult:
    """Create the (single) Kill Case for an opportunity. Idempotent per opportunity."""
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {sorted(DISPOSITIONS)}")
    digest = canonical_payload_hash({"disposition": disposition})
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM opportunity WHERE id = %s", (opportunity_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"opportunity {opportunity_id} does not exist")
        venture_id = row[0]
        cur.execute(
            "SELECT id, content_hash FROM kill_case WHERE opportunity_id = %s", (opportunity_id,)
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != digest:
                raise IdempotencyConflictError(
                    f"kill case for opportunity {opportunity_id} already exists with a different disposition"
                )
            return KillCaseResult(existing[0], created=False)
        cur.execute(
            "INSERT INTO kill_case (opportunity_id, venture_id, kill_case_key, disposition, content_hash) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (opportunity_id, venture_id, kill_case_key, disposition, digest),
        )
        kill_case_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="research.kill_case_created", actor=actor, venture_id=venture_id,
            payload={"kill_case_id": str(kill_case_id), "opportunity_id": str(opportunity_id)},
        )
    return KillCaseResult(kill_case_id, created=True)


def add_dimension(
    conn, *, kill_case_id: str, dimension: str, assessment: str, rationale: str,
    actor: str = "research",
) -> DimensionResult:
    """Add a categorical assessment for one dimension. Idempotent; changed data conflicts."""
    if dimension not in REQUIRED_DIMENSIONS:
        raise ValueError(f"dimension must be one of {sorted(REQUIRED_DIMENSIONS)}")
    if assessment not in ASSESSMENTS:
        raise ValueError(f"assessment must be one of {sorted(ASSESSMENTS)} (categorical, not numeric)")
    if not rationale:
        raise ValueError("rationale is required")
    digest = canonical_payload_hash({"assessment": assessment, "rationale": rationale})
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM kill_case WHERE id = %s", (kill_case_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"kill case {kill_case_id} does not exist")
        venture_id = row[0]
        cur.execute(
            "SELECT id, content_hash FROM kill_case_dimension WHERE kill_case_id = %s AND dimension = %s",
            (kill_case_id, dimension),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != digest:
                raise IdempotencyConflictError(
                    f"dimension {dimension} already assessed differently on this kill case"
                )
            return DimensionResult(existing[0], created=False)
        cur.execute(
            "INSERT INTO kill_case_dimension (kill_case_id, venture_id, dimension, assessment, rationale, content_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (kill_case_id, venture_id, dimension, assessment, rationale, digest),
        )
        dimension_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="research.kill_case_dimension_added", actor=actor, venture_id=venture_id,
            payload={"kill_case_id": str(kill_case_id), "dimension": dimension, "assessment": assessment},
        )
    return DimensionResult(dimension_id, created=True)


def link_dimension_claim(conn, *, dimension_id: str, claim_id: str, actor: str = "research") -> bool:
    """Link a Kill Case dimension to a Claim it depends on. Cross-venture rejected (FK)."""
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM kill_case_dimension WHERE id = %s", (dimension_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"kill case dimension {dimension_id} does not exist")
        venture_id = row[0]
        cur.execute(
            "INSERT INTO kill_case_dimension_claim (dimension_id, claim_id, venture_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (dimension_id, claim_id) DO NOTHING RETURNING id",
            (dimension_id, claim_id, venture_id),
        )
        if cur.fetchone() is None:
            return False
        audit.record_event(
            cur, event_type="research.kill_case_dimension_claim_linked", actor=actor, venture_id=venture_id,
            payload={"dimension_id": str(dimension_id), "claim_id": str(claim_id)},
        )
    return True


def missing_dimensions(conn, kill_case_id: str) -> set:
    """Return the required dimensions not yet assessed on this kill case."""
    with conn.cursor() as cur:
        cur.execute("SELECT dimension FROM kill_case_dimension WHERE kill_case_id = %s", (kill_case_id,))
        present = {r[0] for r in cur.fetchall()}
    return set(REQUIRED_DIMENSIONS) - present
