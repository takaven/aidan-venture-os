"""Gate 3 HELD-OUT eval set — scenarios deliberately kept separate from the
development fixtures, exercising the SAME production logic (nextaction.recommend
+ commitment.commit_recommendation) with no production special-casing and no
injected expected output. These prove the Gate 3 invariants generalize beyond
the development cases; they do NOT prove commercial success.
"""
from __future__ import annotations

from aidan_core import commitment, nextaction, ventures
from aidan_core.research import opportunities

import gate3_fixtures as g


def _recommend(conn, vid, opp):
    return nextaction.recommend(conn, vid, opp, recommendation_key="h")


def test_H1_strong_buyer_pain_but_acquisition_unresolved_is_not_build(migrated):
    vid = g.venture(migrated, "h1")
    opp = g.opportunity(migrated, vid)
    g.supported_claim(migrated, vid, opp)                       # documented buyer pain
    problem = g.critical_assumption(migrated, vid, opp, key="problem", importance="MEDIUM")
    tp = g.discriminating_test(migrated, vid, opp, problem, tkey="tp", hkey="hp")
    g.result(migrated, tp, rkey="rp", score=2)                  # problem validated
    acq = g.critical_assumption(migrated, vid, opp, key="acquisition")
    g.discriminating_test(migrated, vid, opp, acq, tkey="ta", hkey="ha")  # acquisition unresolved
    assert _recommend(migrated, vid, opp).action_type != "BUILD"


def test_H2_strong_acquisition_weak_wtp_is_not_build(migrated):
    vid = g.venture(migrated, "h2")
    opp = g.opportunity(migrated, vid)
    acq = g.critical_assumption(migrated, vid, opp, key="acquisition")
    ta = g.discriminating_test(migrated, vid, opp, acq, tkey="ta", hkey="ha")
    g.result(migrated, ta, rkey="ra", score=2, measurement="OUTREACH_RESPONSE")  # acquisition PASS
    wtp = g.critical_assumption(migrated, vid, opp, key="wtp")
    g.discriminating_test(migrated, vid, opp, wtp, tkey="tw", hkey="hw")          # WTP unresolved
    assert _recommend(migrated, vid, opp).action_type != "BUILD"


def test_H3_apparent_opportunity_with_decisive_kill_is_kill(migrated):
    vid = g.venture(migrated, "h3")
    opp = g.opportunity(migrated, vid)
    g.supported_claim(migrated, vid, opp)
    strong = g.critical_assumption(migrated, vid, opp, key="strong")
    ts = g.discriminating_test(migrated, vid, opp, strong, tkey="ts", hkey="hs")
    g.result(migrated, ts, rkey="rs", score=2, wtp="SIGNED_COMMITMENT")  # looks strong
    risky = g.critical_assumption(migrated, vid, opp, key="risky")
    tk = g.discriminating_test(migrated, vid, opp, risky, tkey="tk", hkey="hk", kill=True)
    assert g.result(migrated, tk, rkey="rk", score=0).outcome == "FAIL"  # decisive kill
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "KILL"
    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "KILL"


def test_H4_unresolved_regulatory_critical_is_validate_or_research(migrated):
    vid = g.venture(migrated, "h4")
    opp = g.opportunity(migrated, vid)
    reg = g.critical_assumption(migrated, vid, opp, key="regulatory")
    g.discriminating_test(migrated, vid, opp, reg, tkey="tr", hkey="hr")  # credible test, no result yet
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type in ("VALIDATE", "RESEARCH_MORE")


def test_H5_genuine_build_ready_builds_without_execution(migrated):
    vid = g.venture(migrated, "h5")
    opp = g.opportunity(migrated, vid)
    g.build_ready(migrated, vid, opp)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "BUILD"
    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "BUILD"
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    assert opportunities.get_status(migrated, opp) == "CANDIDATE"
    assert g.global_count(migrated, "execution_attempt") == 0
    assert g.global_count(migrated, "proof_receipt") == 0
    assert out.resulting_action_id is None  # no amount supplied -> no BUILD ActionRequest fabricated


def test_H6_build_nothing_single_signal(migrated):
    # Only budget + a resolved non-critical signal; the critical uncertainty is open.
    vid = g.venture(migrated, "h6", grant=5000)
    opp = g.opportunity(migrated, vid)
    minor = g.critical_assumption(migrated, vid, opp, key="minor", importance="MEDIUM")
    tm = g.discriminating_test(migrated, vid, opp, minor, tkey="tm", hkey="hm")
    g.result(migrated, tm, rkey="rm", score=2)
    g.critical_assumption(migrated, vid, opp, key="core")  # unresolved CRITICAL, no test
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"
