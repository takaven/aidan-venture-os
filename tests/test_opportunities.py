"""Opportunities: candidate guard, mandatory Kill Case, insufficient/no-credible,
contradiction preservation, security boundary, idempotency, persistence."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import IdempotencyConflictError, OpportunityNotReadyError
from aidan_core.research import assumptions, claims, killcase, observations, opportunities, sources
from aidan_core.research.adapters import AcquiredSource

from conftest import full_kill_case, research_claim

UTC = timezone.utc


def _assumption(conn, vid, *, key="a1"):
    return assumptions.create_assumption(
        conn, vid, proposition="p", assumption_key=key, importance="HIGH", confidence="LOW",
        consequence_if_false="c", cheapest_test="interview 5 buyers",
    ).assumption_id


def _draft(conn, vid, *, key="o1", buyer="B", problem="P", critical="U"):
    return opportunities.create_opportunity(
        conn, vid, opportunity_key=key, buyer_hypothesis=buyer, problem_hypothesis=problem,
        acquisition_hypothesis="A", critical_unknown=critical,
    ).opportunity_id


def _ready(conn, vid, *, key="o1", stance="SUPPORTS"):
    """A draft with one linked claim + assumption + hypotheses (no kill case yet)."""
    opp = _draft(conn, vid, key=key)
    cid, _ = research_claim(conn, vid, key=f"rc-{key}", stance=stance)
    opportunities.link_claim(conn, opportunity_id=opp, claim_id=cid)
    opportunities.link_assumption(conn, opportunity_id=opp, assumption_id=_assumption(conn, vid, key=f"a-{key}"))
    return opp, cid


def test_draft_has_no_side_effects(migrated):
    vid = ventures.create_venture(migrated, slug="op-draft")
    opp = _draft(migrated, vid)
    assert opportunities.get_status(migrated, opp) == "DRAFT"
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM action_request")
        assert cur.fetchone()[0] == 0


def test_mandatory_kill_case_before_candidate(migrated):
    vid = ventures.create_venture(migrated, slug="op-kcguard")
    opp, _ = _ready(migrated, vid)
    # No kill case -> rejected.
    with pytest.raises(OpportunityNotReadyError):
        opportunities.finalize_candidate(migrated, opp)
    # Partial kill case -> rejected.
    kc = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS").kill_case_id
    killcase.add_dimension(migrated, kill_case_id=kc, dimension="REGULATION", assessment="LOW_RISK", rationale="r")
    with pytest.raises(OpportunityNotReadyError):
        opportunities.finalize_candidate(migrated, opp)
    # Complete all dimensions -> finalize succeeds, and is idempotent.
    for dim in killcase.REQUIRED_DIMENSIONS:
        killcase.add_dimension(migrated, kill_case_id=kc, dimension=dim, assessment="MATERIAL_RISK", rationale="r")
    assert opportunities.finalize_candidate(migrated, opp) == "CANDIDATE"
    assert opportunities.finalize_candidate(migrated, opp) == "CANDIDATE"
    assert opportunities.get_status(migrated, opp) == "CANDIDATE"


def test_finalize_requires_claim_and_assumption(migrated):
    vid = ventures.create_venture(migrated, slug="op-req")
    # Hypotheses + kill case but no claim/assumption links.
    opp = _draft(migrated, vid)
    full_kill_case(migrated, opp)
    with pytest.raises(OpportunityNotReadyError):
        opportunities.finalize_candidate(migrated, opp)


def test_insufficient_evidence_is_valid(migrated):
    vid = ventures.create_venture(migrated, slug="op-insuf")
    opp = _draft(migrated, vid, critical=None)  # sparse
    assert opportunities.mark_insufficient_evidence(migrated, opp, reason="no buyer signal") == "INSUFFICIENT_EVIDENCE"
    with migrated.cursor() as cur:
        cur.execute("SELECT status_reason FROM opportunity WHERE id = %s", (opp,))
        assert cur.fetchone()[0] == "no buyer signal"
        cur.execute("SELECT count(*) FROM claim WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 0  # no fake evidence invented
        cur.execute("SELECT count(*) FROM action_request")
        assert cur.fetchone()[0] == 0


def test_no_credible_opportunity_is_valid(migrated):
    vid = ventures.create_venture(migrated, slug="op-none")
    opportunities.record_research_result(migrated, vid, result_key="r1", outcome="NO_CREDIBLE_OPPORTUNITY", reason="weak mandate")
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM opportunity WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 0  # zero candidates is valid
        cur.execute("SELECT outcome FROM research_result WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == "NO_CREDIBLE_OPPORTUNITY"
        cur.execute("SELECT count(*) FROM investment_decision_record")
        assert cur.fetchone()[0] == 0


def test_candidate_with_contradicted_claim_preserves_uncertainty(migrated):
    vid = ventures.create_venture(migrated, slug="op-disputed")
    opp = _draft(migrated, vid)
    # Build a DISPUTED claim.
    cid, obs_s = research_claim(migrated, vid, key="rc", stance="SUPPORTS")
    src = sources.ingest(migrated, vid, AcquiredSource(locator="L", source_type="WEB_PAGE", content="x",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), retrieved_by="a", acquisition_key="src-c")).evidence_record_id
    obs_c = observations.create_observation(migrated, vid, source_evidence_id=src, statement="contra", observation_key="obs-c").evidence_record_id
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs_c, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "DISPUTED"

    opportunities.link_claim(migrated, opportunity_id=opp, claim_id=cid)
    opportunities.link_assumption(migrated, opportunity_id=opp, assumption_id=_assumption(migrated, vid))
    full_kill_case(migrated, opp)
    assert opportunities.finalize_candidate(migrated, opp) == "CANDIDATE"  # structural completeness only
    # Uncertainty preserved and exposed.
    assert claims.claim_state(migrated, cid) == "DISPUTED"
    summary = opportunities.evidence_summary(migrated, opp)
    disputed = [c for c in summary["claims"] if c["claim_id"] == cid][0]
    assert disputed["state"] == "DISPUTED"
    assert {p["stance"] for p in disputed["paths"]} == {"SUPPORTS", "CONTRADICTS"}


def test_research_reasoning_has_no_governance_authority(migrated):
    vid = ventures.create_venture(migrated, slug="op-sec")
    opp, _ = _ready(migrated, vid)
    full_kill_case(migrated, opp)
    opportunities.finalize_candidate(migrated, opp)
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    with migrated.cursor() as cur:
        for table in ("policy_decision", "action_request", "kill_switch", "proof_receipt", "investment_decision_record"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table
        cur.execute("SELECT count(*) FROM budget_account WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 0


def test_opportunity_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="op-idem")
    a = opportunities.create_opportunity(migrated, vid, opportunity_key="k", buyer_hypothesis="B")
    b = opportunities.create_opportunity(migrated, vid, opportunity_key="k", buyer_hypothesis="B")
    assert b.created is False and b.opportunity_id == a.opportunity_id
    with pytest.raises(IdempotencyConflictError):
        opportunities.create_opportunity(migrated, vid, opportunity_key="k", buyer_hypothesis="DIFFERENT")


def test_candidate_persists_after_reconnect(migrated):
    url = os.environ["DATABASE_URL"]
    c = psycopg.connect(url, autocommit=True)
    try:
        vid = ventures.create_venture(c, slug="op-persist")
        opp, cid = _ready(c, vid)
        full_kill_case(c, opp)
        opportunities.finalize_candidate(c, opp)
    finally:
        c.close()
    c = psycopg.connect(url, autocommit=True)
    try:
        assert opportunities.get_status(c, opp) == "CANDIDATE"
        summary = opportunities.evidence_summary(c, opp)
        assert len(summary["claims"]) == 1 and len(summary["kill_case"]["dimensions"]) == 11
        assert len(summary["assumptions"]) == 1
    finally:
        c.close()
