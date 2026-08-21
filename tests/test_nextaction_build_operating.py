"""Gate 8 — allocator BUILD/MARKET precedence: BUILD is a PRE-operational action.

Reproduces and guards the fix for the defect the phase-composed join exposed: a validated CANDIDATE
opportunity kept being recommended BUILD even after its venture reached OPERATING, so MARKET was
structurally unreachable. Invariant: BUILD (step 4) fires only while the venture is not yet OPERATING;
once OPERATING (already built + deployed) it yields, and the OPERATING venture can reach the market
loop. Uses real production APIs + the Gate-3 kernel builders (no fabricated decision, no market/deploy
machinery) so the guard is proven in isolation from the heavier full-join test.
"""
from __future__ import annotations

from aidan_core import lifecycle, nextaction, validation

import gate3_fixtures as g


def _validated_candidate(migrated, slug):
    """A CANDIDATE opportunity with a resolved CRITICAL assumption -> BUILD-eligible at step 4."""
    vid = g.venture(migrated, slug)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    validation.record_result(migrated, validation_test_id=tid, result_key="p", observed_value={"score": 1})
    return vid, opp


def _advance(migrated, vid, *states):
    for s in states:
        lifecycle.transition(migrated, vid, s, actor="op")


def test_build_fires_while_discovered(migrated):
    # case 1: validated candidate, pre-build lifecycle -> BUILD
    vid, opp = _validated_candidate(migrated, "bo-disc")
    assert nextaction.recommend(migrated, vid, opp, recommendation_key="r").action_type == "BUILD"


def test_build_still_eligible_during_building(migrated):
    # case 2: BUILD in progress (not yet OPERATING) stays BUILD — never jumps to MARKET
    vid, opp = _validated_candidate(migrated, "bo-bld")
    _advance(migrated, vid, "VALIDATING", "BUILDING")
    assert nextaction.recommend(migrated, vid, opp, recommendation_key="r").action_type == "BUILD"


def test_build_suppressed_once_operating(migrated):
    # THE FIX (case 4): once OPERATING, a validated opportunity is no longer recommended BUILD; with no
    # market basis yet, doctrine falls to HOLD (never a fabricated MARKET).
    vid, opp = _validated_candidate(migrated, "bo-op")
    _advance(migrated, vid, "VALIDATING", "BUILDING", "OPERATING")
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r")
    assert rec.action_type != "BUILD"
    assert rec.action_type == "HOLD"


def test_operating_suppression_is_durable_across_reload(migrated):
    # case 7: the decision is derived from durable lifecycle_state — a fresh connection sees the same.
    import os
    import psycopg
    vid, opp = _validated_candidate(migrated, "bo-durable")
    _advance(migrated, vid, "VALIDATING", "BUILDING", "OPERATING")
    fresh = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        assert nextaction.recommend(fresh, vid, opp, recommendation_key="r").action_type != "BUILD"
    finally:
        fresh.close()
