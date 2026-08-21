"""Gate 8 — CORE TOOL proof: the mandate-origin JOIN (Track B intelligence -> governed allocation).

Every other loop suite starts DOWNSTREAM of research — it hand-inserts the opportunity/validation
rows and seeds market evidence. This suite instead starts from ONLY a Venture Mandate, runs the real
research orchestration to PRODUCE the opportunity + adversarial kill case + assumption, and proves
that mandate-origin intelligence composes end-to-end into a governed capital-allocation DECISION via
the real production APIs (nextaction.recommend -> commitment.commit_recommendation). No preseeded
opportunity, no injected decision, deterministic fakes only (no network, no provider, no send).

It also proves the governed BOUNDARY between research and allocation is real: research output alone is
deliberately NOT enough to commit — the allocator returns a non-convertible RESEARCH_MORE until a
validation authority authors a discriminating test. That boundary is by design, not a defect.
"""
from __future__ import annotations

import pytest

from aidan_core import commitment, nextaction, validation
from aidan_core.errors import RecommendationNotConvertibleError
from aidan_core.research import orchestration

from research_fixtures import (
    ReplayAdapter,
    ScriptedProposer,
    acquired,
    build_credible,
    make_mandate,
)

_Q = "How burdensome is SMB reconciliation and will teams pay to automate it?"
_SRC = "SMB teams spend 5+ hours on reconciliation weekly."


def _research_from_mandate(migrated, slug):
    """ONLY a Venture Mandate -> one real research run -> (opportunity_id, assumption_id)."""
    vid, ver, mandate = make_mandate(migrated, slug)
    adapter = ReplayAdapter({_Q: [acquired(_SRC, key="s1")]})
    proposer = ScriptedProposer([_Q], build_credible)
    r = orchestration.run_research(migrated, venture_id=vid, mandate_version=ver, mandate_content=mandate,
                                   run_key="rr1", adapter=adapter, proposer=proposer)
    # the mandate produced exactly one finalized CANDIDATE opportunity + a HIGH assumption
    opp_id = next(oid for oid, status in r.opportunity_statuses.items() if status == "CANDIDATE")
    assert r.assumption_ids, "research produced no assumption from the mandate"
    return vid, opp_id, r.assumption_ids[0], r


def _author_discriminating_validation(migrated, vid, opp_id, aid):
    """A validation authority authors a structured, discriminating test for the unresolved assumption
    (the governed step between research and allocation) — using real production validation APIs."""
    hid = validation.create_hypothesis(
        migrated, vid, opportunity_id=opp_id, statement="teams will pay to automate reconciliation",
        hypothesis_key="h1", assumption_id=aid).hypothesis_id
    validation.create_test(
        migrated, vid, validation_hypothesis_id=hid, test_key="t1", test_type="INTERVIEW", method="m",
        success_criterion="score>=1", evidence_required="notes", success_metric="score",
        success_comparator="GTE", success_threshold=1)


def test_mandate_research_produces_opportunity_and_kill_case(migrated):
    # Track B origin: a bare Venture Mandate yields a real opportunity + adversarial kill case.
    vid, opp_id, aid, r = _research_from_mandate(migrated, "join-origin")
    assert opp_id and aid
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM opportunity WHERE id = %s", (opp_id,))
        assert cur.fetchone()[0] == "CANDIDATE"
        cur.execute("SELECT importance FROM assumption WHERE id = %s", (aid,))
        assert cur.fetchone()[0] in ("CRITICAL", "HIGH")   # an unresolved key assumption exists


def test_mandate_research_alone_is_non_convertible(migrated):
    # governed BOUNDARY: research output alone cannot commit capital — the allocator asks for more
    # evidence, and that recommendation is deliberately non-convertible (not a defect).
    vid, opp_id, aid, _r = _research_from_mandate(migrated, "join-boundary")
    rec = nextaction.recommend(migrated, vid, opp_id, recommendation_key="k0")
    assert rec.action_type == "RESEARCH_MORE"
    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)


def test_mandate_only_joins_into_governed_validate_decision(migrated):
    # THE JOIN: mandate -> research -> validation authored -> the allocator selects the highest-value
    # next action (VALIDATE the cheapest discriminating test) -> committed as a governed
    # investment_decision_record. Proven from ONLY a mandate, through the real production chain.
    vid, opp_id, aid, _r = _research_from_mandate(migrated, "join-validate")
    _author_discriminating_validation(migrated, vid, opp_id, aid)

    rec = nextaction.recommend(migrated, vid, opp_id, recommendation_key="k1")
    assert rec.action_type == "VALIDATE"
    prov = nextaction.provenance(migrated, rec.recommendation_id)
    assert prov["selected_validation_test_id"] is not None   # a concrete discriminating test was chosen

    res = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert res.decision == "VALIDATE"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s "
                    "AND source_recommendation_id = %s", (vid, rec.recommendation_id))
        assert cur.fetchone()[0] == 1                         # a governed capital decision, mandate-origin
