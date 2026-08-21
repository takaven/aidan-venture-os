"""Gate 8 — allocator BUILD/MARKET precedence: BUILD is a PRE-operational action.

Reproduces and guards the fix for the defect the phase-composed join exposed: a validated CANDIDATE
opportunity kept being recommended BUILD even after its venture reached OPERATING, so MARKET was
structurally unreachable. Invariant: BUILD (step 4) fires only while the venture is not yet OPERATING;
once OPERATING (already built + deployed) it yields, and the OPERATING venture can reach the market
loop. Uses real production APIs (research origin -> validation) + the governed lifecycle authority; no
build/deploy or market machinery, so the guard is proven in isolation from the heavier full-join test.
"""
from __future__ import annotations

from aidan_core import lifecycle, nextaction, validation, ventures
from aidan_core.research import orchestration, sources

from research_fixtures import ReplayAdapter, ScriptedProposer, acquired, build_credible

_Q = "How burdensome is SMB reconciliation?"
_SRC = "SMB teams spend 5+ hours on reconciliation weekly."


def _build_eligible(migrated, slug):
    """A CANDIDATE opportunity (from real research) whose HIGH assumption is resolved by a PASS ->
    BUILD-eligible at allocator step 4, before any lifecycle advancement."""
    vid = ventures.create_venture(migrated, slug=slug, autonomy_level=1)
    ventures.append_mandate_version(migrated, vid, content_hash=sources.content_hash("M"))
    r = orchestration.run_research(
        migrated, venture_id=vid, mandate_version=1, mandate_content="M", run_key="rr",
        adapter=ReplayAdapter({_Q: [acquired(_SRC, key="s1")]}), proposer=ScriptedProposer([_Q], build_credible))
    opp = next(oid for oid, st in r.opportunity_statuses.items() if st == "CANDIDATE")
    a1 = r.assumption_ids[0]
    h = validation.create_hypothesis(migrated, vid, opportunity_id=opp, assumption_id=a1,
                                     statement="resolved", hypothesis_key="h")
    t = validation.create_test(migrated, vid, validation_hypothesis_id=h.hypothesis_id, test_key="t",
                               test_type="INTERVIEW", method="m", success_criterion="score>=1",
                               evidence_required="notes", success_metric="score",
                               success_comparator="GTE", success_threshold=1)
    validation.record_result(migrated, validation_test_id=t.test_id, result_key="p",
                             observed_value={"score": 1})
    return vid, opp


def _advance(migrated, vid, *states):
    for s in states:
        lifecycle.transition(migrated, vid, s, actor="op")


def test_build_fires_while_discovered(migrated):
    # case 1: validated candidate, pre-build lifecycle -> BUILD
    vid, opp = _build_eligible(migrated, "bo-disc")
    assert nextaction.recommend(migrated, vid, opp, recommendation_key="r").action_type == "BUILD"


def test_build_still_eligible_during_building(migrated):
    # case 2: BUILD in progress (committed BUILD, not yet OPERATING) stays BUILD — never jumps to MARKET
    vid, opp = _build_eligible(migrated, "bo-bld")
    _advance(migrated, vid, "VALIDATING", "BUILDING")
    assert nextaction.recommend(migrated, vid, opp, recommendation_key="r").action_type == "BUILD"


def test_build_suppressed_once_operating(migrated):
    # THE FIX (case 4): once OPERATING, a validated opportunity is no longer recommended BUILD; with no
    # market basis yet, doctrine falls to HOLD (never a fabricated MARKET).
    vid, opp = _build_eligible(migrated, "bo-op")
    _advance(migrated, vid, "VALIDATING", "BUILDING", "OPERATING")
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r")
    assert rec.action_type != "BUILD"
    assert rec.action_type == "HOLD"


def test_operating_suppression_is_durable_across_reload(migrated):
    # case 7: the decision is derived from durable lifecycle_state — a fresh connection sees the same.
    import os
    import psycopg
    vid, opp = _build_eligible(migrated, "bo-durable")
    _advance(migrated, vid, "VALIDATING", "BUILDING", "OPERATING")
    fresh = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        assert nextaction.recommend(fresh, vid, opp, recommendation_key="r").action_type != "BUILD"
    finally:
        fresh.close()
