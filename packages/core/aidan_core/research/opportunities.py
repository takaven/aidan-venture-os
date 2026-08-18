"""Opportunities — research candidates. NOT investment approval, NOT BUILD.

An Opportunity links back to Claims/Interpretations/Assumptions (it never
duplicates evidence). Its content is immutable; only its research status
transitions, through guarded operations. A serious CANDIDATE cannot be finalized
without structural completeness AND a complete adversarial Kill Case. Reaching
CANDIDATE has no capital/investment/ActionRequest side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError, OpportunityNotReadyError
from . import claims as claims_mod
from . import killcase


@dataclass(frozen=True)
class OpportunityResult:
    opportunity_id: str
    created: bool


def create_opportunity(
    conn, venture_id: str, *, opportunity_key: str, buyer_hypothesis: Optional[str] = None,
    problem_hypothesis: Optional[str] = None, acquisition_hypothesis: Optional[str] = None,
    critical_unknown: Optional[str] = None, actor: str = "research",
) -> OpportunityResult:
    """Create a DRAFT opportunity. Idempotent per (venture, opportunity_key)."""
    if not opportunity_key:
        raise ValueError("opportunity_key is required")
    digest = canonical_payload_hash({
        "buyer": buyer_hypothesis or "", "problem": problem_hypothesis or "",
        "acquisition": acquisition_hypothesis or "", "critical_unknown": critical_unknown or "",
    })
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT id, payload_hash FROM opportunity WHERE venture_id = %s AND opportunity_key = %s",
            (venture_id, opportunity_key),
        )
        row = cur.fetchone()
        if row is not None:
            if row[1] != digest:
                raise IdempotencyConflictError(
                    f"opportunity key {opportunity_key!r} reused with different content"
                )
            return OpportunityResult(row[0], created=False)
        cur.execute(
            """
            INSERT INTO opportunity
                (venture_id, opportunity_key, buyer_hypothesis, problem_hypothesis,
                 acquisition_hypothesis, critical_unknown, payload_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, opportunity_key, buyer_hypothesis, problem_hypothesis,
             acquisition_hypothesis, critical_unknown, digest),
        )
        opportunity_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="research.opportunity_created", actor=actor, venture_id=venture_id,
            payload={"opportunity_id": str(opportunity_id), "status": "DRAFT"},
        )
    return OpportunityResult(opportunity_id, created=True)


def _link(conn, table, col, opportunity_id, other_id, event, actor):
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id FROM opportunity WHERE id = %s", (opportunity_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"opportunity {opportunity_id} does not exist")
        venture_id = row[0]
        cur.execute(
            f"INSERT INTO {table} (opportunity_id, {col}, venture_id) VALUES (%s, %s, %s) "
            f"ON CONFLICT (opportunity_id, {col}) DO NOTHING RETURNING id",
            (opportunity_id, other_id, venture_id),
        )
        if cur.fetchone() is None:
            return False
        audit.record_event(
            cur, event_type=event, actor=actor, venture_id=venture_id,
            payload={"opportunity_id": str(opportunity_id), col: str(other_id)},
        )
    return True


def link_claim(conn, *, opportunity_id, claim_id, actor="research") -> bool:
    return _link(conn, "opportunity_claim", "claim_id", opportunity_id, claim_id,
                 "research.opportunity_claim_linked", actor)


def link_assumption(conn, *, opportunity_id, assumption_id, actor="research") -> bool:
    return _link(conn, "opportunity_assumption", "assumption_id", opportunity_id, assumption_id,
                 "research.opportunity_assumption_linked", actor)


def link_interpretation(conn, *, opportunity_id, interpretation_id, actor="research") -> bool:
    return _link(conn, "opportunity_interpretation", "interpretation_id", opportunity_id, interpretation_id,
                 "research.opportunity_interpretation_linked", actor)


def get_status(conn, opportunity_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM opportunity WHERE id = %s", (opportunity_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"opportunity {opportunity_id} does not exist")
        return row[0]


def finalize_candidate(conn, opportunity_id: str, *, actor: str = "research") -> str:
    """Guarded DRAFT -> CANDIDATE. Requires structural completeness + a complete Kill Case.

    Does NOT require claims to be SUPPORTED (contradictory evidence stays visible)
    and imposes no numeric threshold. No investment/capital/ActionRequest effect.
    """
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT status, venture_id, buyer_hypothesis, problem_hypothesis, critical_unknown "
            "FROM opportunity WHERE id = %s FOR UPDATE",
            (opportunity_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"opportunity {opportunity_id} does not exist")
        status, venture_id, buyer, problem, critical = row
        if status == "CANDIDATE":
            return "CANDIDATE"  # idempotent
        if status != "DRAFT":
            raise OpportunityNotReadyError(f"cannot finalize from status {status}")

        if not buyer or not problem or not critical:
            raise OpportunityNotReadyError("buyer, problem and critical_unknown hypotheses are required")
        cur.execute("SELECT count(*) FROM opportunity_claim WHERE opportunity_id = %s", (opportunity_id,))
        if cur.fetchone()[0] == 0:
            raise OpportunityNotReadyError("at least one linked Claim is required")
        cur.execute("SELECT count(*) FROM opportunity_assumption WHERE opportunity_id = %s", (opportunity_id,))
        if cur.fetchone()[0] == 0:
            raise OpportunityNotReadyError("at least one linked Assumption is required")

        cur.execute("SELECT id FROM kill_case WHERE opportunity_id = %s", (opportunity_id,))
        kc = cur.fetchone()
        if kc is None:
            raise OpportunityNotReadyError("an adversarial Kill Case is required")
        cur.execute("SELECT dimension FROM kill_case_dimension WHERE kill_case_id = %s", (kc[0],))
        present = {r[0] for r in cur.fetchall()}
        missing = set(killcase.REQUIRED_DIMENSIONS) - present
        if missing:
            raise OpportunityNotReadyError(f"kill case missing dimensions: {sorted(missing)}")

        cur.execute(
            "UPDATE opportunity SET status = 'CANDIDATE', updated_at = now() WHERE id = %s",
            (opportunity_id,),
        )
        audit.record_event(
            cur, event_type="research.opportunity_finalized_candidate", actor=actor, venture_id=venture_id,
            payload={"opportunity_id": str(opportunity_id)},
        )
    return "CANDIDATE"


def _transition(conn, opportunity_id, target, reason, event, actor, from_states):
    with db.transaction(conn) as cur:
        cur.execute("SELECT status, venture_id FROM opportunity WHERE id = %s FOR UPDATE", (opportunity_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"opportunity {opportunity_id} does not exist")
        status, venture_id = row
        if status == target:
            return target
        if status not in from_states:
            raise OpportunityNotReadyError(f"cannot transition {status} -> {target}")
        cur.execute(
            "UPDATE opportunity SET status = %s, status_reason = %s, updated_at = now() WHERE id = %s",
            (target, reason, opportunity_id),
        )
        audit.record_event(
            cur, event_type=event, actor=actor, venture_id=venture_id,
            payload={"opportunity_id": str(opportunity_id), "reason": reason},
        )
    return target


def mark_insufficient_evidence(conn, opportunity_id: str, *, reason: str, actor: str = "research") -> str:
    """DRAFT -> INSUFFICIENT_EVIDENCE. A valid research outcome; no Kill Case required."""
    return _transition(conn, opportunity_id, "INSUFFICIENT_EVIDENCE", reason,
                       "research.opportunity_insufficient_evidence", actor, {"DRAFT"})


def kill(conn, opportunity_id: str, *, reason: str, actor: str = "research") -> str:
    """DRAFT/CANDIDATE -> KILLED (Gate 2 research rejection; not an investment decision)."""
    return _transition(conn, opportunity_id, "KILLED", reason,
                       "research.opportunity_killed", actor, {"DRAFT", "CANDIDATE"})


def record_research_result(
    conn, venture_id: str, *, result_key: str, outcome: str, reason: Optional[str] = None,
    actor: str = "research",
) -> str:
    """Record a durable research-context outcome (e.g. NO_CREDIBLE_OPPORTUNITY)."""
    valid = {"OPPORTUNITIES_FOUND", "NO_CREDIBLE_OPPORTUNITY", "INSUFFICIENT_EVIDENCE"}
    if outcome not in valid:
        raise ValueError(f"outcome must be one of {sorted(valid)}")
    with db.transaction(conn) as cur:
        cur.execute(
            "INSERT INTO research_result (venture_id, result_key, outcome, reason) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (venture_id, result_key, outcome, reason),
        )
        result_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="research.result_recorded", actor=actor, venture_id=venture_id,
            payload={"result_id": str(result_id), "outcome": outcome},
        )
    return result_id


def evidence_summary(conn, opportunity_id: str) -> dict:
    """Structured provenance: Opportunity -> Claims/Assumptions/Kill Case -> evidence."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT venture_id, status, buyer_hypothesis, problem_hypothesis, acquisition_hypothesis, "
            "critical_unknown FROM opportunity WHERE id = %s",
            (opportunity_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"opportunity {opportunity_id} does not exist")
        _venture, status, buyer, problem, acquisition, critical = row

        cur.execute("SELECT claim_id FROM opportunity_claim WHERE opportunity_id = %s ORDER BY created_at", (opportunity_id,))
        claim_ids = [r[0] for r in cur.fetchall()]

        cur.execute(
            "SELECT a.id, a.proposition, a.importance, a.confidence, a.consequence_if_false, a.cheapest_test "
            "FROM opportunity_assumption oa JOIN assumption a ON a.id = oa.assumption_id "
            "WHERE oa.opportunity_id = %s ORDER BY oa.created_at",
            (opportunity_id,),
        )
        assumptions = [
            {"assumption_id": r[0], "proposition": r[1], "importance": r[2], "confidence": r[3],
             "consequence_if_false": r[4], "cheapest_test": r[5]}
            for r in cur.fetchall()
        ]

        cur.execute("SELECT id, disposition FROM kill_case WHERE opportunity_id = %s", (opportunity_id,))
        kc = cur.fetchone()
        kill_case = None
        if kc is not None:
            cur.execute(
                "SELECT id, dimension, assessment, rationale FROM kill_case_dimension "
                "WHERE kill_case_id = %s ORDER BY created_at",
                (kc[0],),
            )
            dims = []
            for d in cur.fetchall():
                cur.execute("SELECT claim_id FROM kill_case_dimension_claim WHERE dimension_id = %s", (d[0],))
                dims.append({
                    "dimension": d[1], "assessment": d[2], "rationale": d[3],
                    "claims": [c[0] for c in cur.fetchall()],
                })
            kill_case = {"kill_case_id": kc[0], "disposition": kc[1], "dimensions": dims}

    # Claim provenance (state + evidence paths) reuses the Slice 2 traversal.
    claim_provenance = [claims_mod.provenance(conn, cid) for cid in claim_ids]

    return {
        "opportunity_id": opportunity_id,
        "status": status,
        "buyer_hypothesis": buyer,
        "problem_hypothesis": problem,
        "acquisition_hypothesis": acquisition,
        "critical_unknown": critical,
        "claims": claim_provenance,
        "assumptions": assumptions,
        "kill_case": kill_case,
    }
