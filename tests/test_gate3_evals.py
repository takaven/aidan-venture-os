"""Gate 3 development eval suite — end-to-end validation -> allocation ->
investment decision -> governed consequential action.

Every case constructs EXPLICIT canonical state via gate3_fixtures and drives the
real production functions (``nextaction.recommend`` then, where relevant,
``commitment.commit_recommendation``). No expected decision is injected into
production; outcomes are observed. These deterministic fixtures prove decision
DISCIPLINE and architecture — never commercial success, live demand, or payment.
Gate 3 performs no execution: after any governed commitment there is no
execution_attempt, no proof_receipt, and no ActionRequest SUCCESS.
"""
from __future__ import annotations

import pytest

from aidan_core import commitment, execution, killswitch, nextaction, ventures
from aidan_core.errors import StaleRecommendationError
from aidan_core.research import claims, opportunities

from conftest import full_kill_case

import gate3_fixtures as g


def _recommend(conn, vid, opp, key="r1"):
    return nextaction.recommend(conn, vid, opp, recommendation_key=key)


# ==========================================================================
# A–R: highest-value next-action doctrine.
# ==========================================================================
def test_A_weak_wtp_is_not_build(migrated):
    vid = g.venture(migrated, "e-A")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    # weak WTP: only STATED_INTEREST, and the criterion is unmet (score<1 -> INCONCLUSIVE),
    # so the critical WTP assumption stays unresolved.
    g.result(migrated, tid, rkey="r", score=0, wtp="STATED_INTEREST")
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"
    assert rec.action_type == "VALIDATE"  # a discriminating test exists for the unresolved critical


def test_B_strong_wtp_weak_acquisition_is_not_build(migrated):
    vid = g.venture(migrated, "e-B")
    opp = g.opportunity(migrated, vid)
    wtp = g.critical_assumption(migrated, vid, opp, key="wtp")
    acq = g.critical_assumption(migrated, vid, opp, key="acq")
    twtp = g.discriminating_test(migrated, vid, opp, wtp, tkey="twtp", hkey="hwtp")
    g.result(migrated, twtp, rkey="rw", score=2, wtp="SIGNED_COMMITMENT")   # WTP PASS
    g.discriminating_test(migrated, vid, opp, acq, tkey="tacq", hkey="hacq")  # acquisition unresolved
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"


def test_C_strong_acquisition_weak_wtp_is_not_build(migrated):
    vid = g.venture(migrated, "e-C")
    opp = g.opportunity(migrated, vid)
    acq = g.critical_assumption(migrated, vid, opp, key="acq")
    wtp = g.critical_assumption(migrated, vid, opp, key="wtp")
    tacq = g.discriminating_test(migrated, vid, opp, acq, tkey="tacq", hkey="hacq")
    g.result(migrated, tacq, rkey="ra", score=2, measurement="LANDING_CONVERSION")  # acquisition PASS
    g.discriminating_test(migrated, vid, opp, wtp, tkey="twtp", hkey="hwtp")   # WTP unresolved
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"


def test_D_critical_unresolved_with_good_test_is_validate(migrated):
    vid = g.venture(migrated, "e-D")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "VALIDATE"
    assert nextaction.provenance(migrated, rec.recommendation_id)["selected_validation_test_id"] == tid


def test_E_critical_unresolved_no_credible_test_is_research_more(migrated):
    vid = g.venture(migrated, "e-E")
    opp = g.opportunity(migrated, vid)
    g.critical_assumption(migrated, vid, opp)  # no test at all
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "RESEARCH_MORE" and rec.reason_code == "INSUFFICIENT_EVIDENCE"


def test_F_prefers_cheaper_of_equivalent_discriminating_tests(migrated):
    vid = g.venture(migrated, "e-F")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    cheap = g.discriminating_test(migrated, vid, opp, aid, tkey="cheap", hkey="hc", max_spend=500)
    g.discriminating_test(migrated, vid, opp, aid, tkey="dear", hkey="hd", max_spend=5000)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "VALIDATE"
    assert nextaction.provenance(migrated, rec.recommendation_id)["selected_validation_test_id"] == cheap


def test_G_prefers_discriminating_over_cheaper_nondiscriminating(migrated):
    vid = g.venture(migrated, "e-G")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.nondiscriminating_test(migrated, vid, opp, aid, tkey="weak", hkey="hw", max_spend=1)  # cheapest but useless
    disc = g.discriminating_test(migrated, vid, opp, aid, tkey="disc", hkey="hd", max_spend=500)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "VALIDATE"
    # NOT simply the cheapest: the discriminating test is chosen despite higher cost.
    assert nextaction.provenance(migrated, rec.recommendation_id)["selected_validation_test_id"] == disc


def test_H_decisive_kill_recommends_and_commits_kill(migrated):
    vid = g.venture(migrated, "e-H")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid, kill=True)
    assert g.result(migrated, tid, rkey="r", score=0).outcome == "FAIL"
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "KILL" and rec.reason_code == "KILL_CRITERION_TRIGGERED"
    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "KILL" and out.resulting_action_id is None
    # A KILL decision is not a lifecycle deletion.
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"


def test_I_contradiction_without_decisive_kill_is_not_build(migrated):
    vid = g.venture(migrated, "e-I")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp, importance="MEDIUM")
    tid = g.discriminating_test(migrated, vid, opp, aid)
    g.result(migrated, tid, rkey="rp", score=2)   # PASS
    g.result(migrated, tid, rkey="ri", score=0)   # INCONCLUSIVE (no kill criterion)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM validation_result WHERE validation_test_id = %s", (tid,))
        assert cur.fetchone()[0] == 2  # both preserved


def test_J_observed_failure_outranks_optimistic_interpretation(migrated):
    vid = g.venture(migrated, "e-J")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid, kill=True)
    res = g.result(migrated, tid, rkey="r", score=0, interpretation="the team still believes strongly")
    assert res.outcome == "FAIL"  # DERIVED from observation, not the optimistic interpretation
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "KILL"


def test_K_hold_when_no_high_value_action(migrated):
    vid = g.venture(migrated, "e-K")
    opp = g.opportunity(migrated, vid)
    g.critical_assumption(migrated, vid, opp, importance="LOW")  # low importance, no test, not candidate
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "HOLD" and rec.reason_code == "NO_HIGH_VALUE_ACTION_NOW"


def test_L_build_ready_recommends_and_commits_build_without_execution(migrated):
    vid = g.venture(migrated, "e-L")
    opp = g.opportunity(migrated, vid)
    g.build_ready(migrated, vid, opp)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "BUILD" and rec.reason_code == "BUILD_CONSIDERATION_READY"
    out = commitment.commit_recommendation(migrated, rec.recommendation_id)  # no amount
    assert out.decision == "BUILD" and out.resulting_action_id is None
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    assert opportunities.get_status(migrated, opp) == "CANDIDATE"
    assert g.global_count(migrated, "execution_attempt") == 0
    assert g.global_count(migrated, "proof_receipt") == 0


def test_M_stale_build_recommendation_cannot_commit(migrated):
    vid = g.venture(migrated, "e-M")
    opp = g.opportunity(migrated, vid)
    aid, _tid = g.build_ready(migrated, vid, opp)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "BUILD"
    # A later decisive FAIL changes the canonical basis.
    ktid = g.discriminating_test(migrated, vid, opp, aid, tkey="k", hkey="hk", kill=True)
    assert g.result(migrated, ktid, rkey="rk", score=0).outcome == "FAIL"
    with pytest.raises(StaleRecommendationError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert g.count(migrated, "investment_decision_record", vid) == 0
    assert g.count(migrated, "action_request", vid) == 0
    assert nextaction.get_recommendation(migrated, rec.recommendation_id)[3] == "BUILD"  # preserved
    fresh = _recommend(migrated, vid, opp, key="r2")
    assert fresh.action_type != "BUILD"  # fresh state reflects the kill


def test_N_validate_governance_allow(migrated):
    vid = g.venture(migrated, "e-N", autonomy=1, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "VALIDATE"
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1)
    assert out.decision == "VALIDATE" and out.policy_decision == "ALLOW"
    g.assert_no_execution(migrated, out.resulting_action_id)


def test_O_validate_governance_require_approval(migrated):
    vid = g.venture(migrated, "e-O", autonomy=0, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=400, required_autonomy=2)
    assert out.policy_decision == "REQUIRE_APPROVAL" and out.approval_id is not None
    from aidan_core import approvals
    assert approvals.get_approval(migrated, out.approval_id)[3] == "PENDING"  # not auto-granted
    assert execution.get_status(migrated, out.resulting_action_id) == "AWAITING_APPROVAL"
    g.assert_no_execution(migrated, out.resulting_action_id)


def test_P_validate_governance_deny_kill_switch(migrated):
    vid = g.venture(migrated, "e-P", autonomy=1, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    killswitch.engage_global(migrated, engaged_by="op")
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1)
    assert out.policy_decision == "DENY"
    assert g.policy_reason(migrated, out.resulting_action_id)[1] == "KILL_SWITCH_GLOBAL"
    g.assert_no_execution(migrated, out.resulting_action_id)


def test_Q_validate_governance_deny_budget(migrated):
    vid = g.venture(migrated, "e-Q", autonomy=1)  # no budget granted
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1)
    assert out.policy_decision == "DENY"
    assert g.policy_reason(migrated, out.resulting_action_id)[1] == "INSUFFICIENT_BUDGET"
    g.assert_no_execution(migrated, out.resulting_action_id)
    assert g.count(migrated, "validation_result", vid) == 0  # denial fabricated no market evidence


def test_R_build_nothing_when_only_structural_readiness(migrated):
    # A critical assumption is resolved by a PASS, but the opportunity is not a
    # CANDIDATE -> BUILD is not licensed; further capital is not the move.
    vid = g.venture(migrated, "e-R")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    g.result(migrated, tid, rkey="r", score=2)  # PASS resolves it, but opp stays DRAFT
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"
    assert opportunities.get_status(migrated, opp) == "DRAFT"


# ==========================================================================
# WTP / acquisition discipline (no universal score/threshold).
# ==========================================================================
@pytest.mark.parametrize("wtp", ["STATED_INTEREST", "STATED_WILLINGNESS", "SIGNED_COMMITMENT", "ACTUAL_PAYMENT"])
def test_wtp_modality_does_not_globally_map_to_build(migrated, wtp):
    # A strong WTP modality alone, with an unmet criterion (score<1 -> not PASS)
    # and thus an unresolved critical assumption, never yields BUILD.
    vid = g.venture(migrated, f"e-wtp-{wtp}")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    g.result(migrated, tid, rkey="r", score=0, wtp=wtp)  # criterion unmet regardless of modality
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"


def test_acquisition_pass_does_not_erase_wtp_gap(migrated):
    vid = g.venture(migrated, "e-acq-only")
    opp = g.opportunity(migrated, vid)
    acq = g.critical_assumption(migrated, vid, opp, key="acq")
    wtp = g.critical_assumption(migrated, vid, opp, key="wtp")
    tacq = g.discriminating_test(migrated, vid, opp, acq, tkey="tacq", hkey="hacq")
    g.result(migrated, tacq, rkey="ra", score=2, measurement="ACQUISITION_COST")  # acquisition PASS
    g.discriminating_test(migrated, vid, opp, wtp, tkey="twtp", hkey="hwtp")  # WTP unresolved
    assert _recommend(migrated, vid, opp).action_type != "BUILD"


# ==========================================================================
# Build-nothing doctrine: a single attractive signal never licenses BUILD.
# ==========================================================================
def _single_signal(migrated, label):
    vid = g.venture(migrated, f"e-bn-{label}", grant=1000)
    opp = g.opportunity(migrated, vid)
    if label == "feasibility_only":
        # a resolved non-critical assumption, but an unresolved CRITICAL one
        feas = g.critical_assumption(migrated, vid, opp, key="feas", importance="MEDIUM")
        tf = g.discriminating_test(migrated, vid, opp, feas, tkey="tf", hkey="hf")
        g.result(migrated, tf, rkey="rf", score=2)
        g.critical_assumption(migrated, vid, opp, key="crit")  # unresolved CRITICAL
    elif label == "buyer_problem_only":
        g.supported_claim(migrated, vid, opp)
        g.critical_assumption(migrated, vid, opp, key="crit")
    elif label == "wtp_pass_only":
        aid = g.critical_assumption(migrated, vid, opp)
        t = g.discriminating_test(migrated, vid, opp, aid)
        g.result(migrated, t, rkey="r", score=2, wtp="SIGNED_COMMITMENT")  # resolved, but opp not CANDIDATE
    elif label == "acquisition_pass_only":
        aid = g.critical_assumption(migrated, vid, opp)
        t = g.discriminating_test(migrated, vid, opp, aid)
        g.result(migrated, t, rkey="r", score=2, measurement="LANDING_CONVERSION")  # not CANDIDATE
    elif label == "budget_only":
        pass  # funded venture, empty opportunity
    elif label == "candidate_only":
        g.critical_assumption(migrated, vid, opp)
        g.supported_claim(migrated, vid, opp)
        g.make_candidate(migrated, vid, opp)  # CANDIDATE but no PASS results (unresolved critical)
    elif label == "kill_case_only":
        g.supported_claim(migrated, vid, opp)
        g.critical_assumption(migrated, vid, opp)
        full_kill_case(migrated, opp)  # complete kill case, but NOT finalized; critical unresolved
    elif label == "pass_with_contradiction":
        aid = g.critical_assumption(migrated, vid, opp, importance="MEDIUM")
        t = g.discriminating_test(migrated, vid, opp, aid)
        g.result(migrated, t, rkey="rp", score=2)   # PASS
        g.result(migrated, t, rkey="ri", score=0)   # INCONCLUSIVE contradiction
    elif label == "unresolved_critical":
        aid = g.critical_assumption(migrated, vid, opp)
        g.discriminating_test(migrated, vid, opp, aid)  # test but no result
    elif label == "decisive_kill":
        aid = g.critical_assumption(migrated, vid, opp)
        t = g.discriminating_test(migrated, vid, opp, aid, kill=True)
        g.result(migrated, t, rkey="r", score=0)  # FAIL
    return vid, opp


@pytest.mark.parametrize("label", [
    "feasibility_only", "buyer_problem_only", "wtp_pass_only", "acquisition_pass_only",
    "budget_only", "candidate_only", "kill_case_only", "pass_with_contradiction",
    "unresolved_critical", "decisive_kill",
])
def test_build_nothing_single_signal_never_builds(migrated, label):
    vid, opp = _single_signal(migrated, label)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD", f"{label} must not license BUILD (got {rec.action_type})"
    # And a BUILD decision cannot be forced from a non-BUILD recommendation.
    from aidan_core.errors import RecommendationNotConvertibleError
    if rec.action_type != "BUILD":
        try:
            out = commitment.commit_recommendation(migrated, rec.recommendation_id, decision="BUILD")
            assert False, f"expected refusal, got {out.decision}"
        except RecommendationNotConvertibleError:
            pass


# ==========================================================================
# Recommendation / decision / action separation (load-bearing).
# ==========================================================================
def test_recommendation_alone_has_no_authority(migrated):
    vid = g.venture(migrated, "e-sep1")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid)
    _recommend(migrated, vid, opp)
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    for table in ("investment_decision_record", "action_request", "capital_entry"):
        assert g.count(migrated, table, vid) == 0
    assert g.global_count(migrated, "policy_decision") == 0


def test_research_more_end_to_end_writes_no_decision(migrated):
    vid = g.venture(migrated, "e-rm")
    opp = g.opportunity(migrated, vid)
    g.critical_assumption(migrated, vid, opp)
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type == "RESEARCH_MORE"
    from aidan_core.errors import RecommendationNotConvertibleError
    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert g.count(migrated, "investment_decision_record", vid) == 0  # no forced HOLD/DO_NOTHING
    assert g.count(migrated, "action_request", vid) == 0
    # The recommendation itself persists.
    assert nextaction.get_recommendation(migrated, rec.recommendation_id)[3] == "RESEARCH_MORE"


# ==========================================================================
# Provenance & append-only history.
# ==========================================================================
def test_decision_provenance_traversal_validate(migrated):
    vid = g.venture(migrated, "e-prov-v", autonomy=1, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=200, required_autonomy=1)
    # InvestmentDecision -> NextActionRecommendation -> Opportunity -> Assumptions.
    with migrated.cursor() as cur:
        cur.execute("SELECT source_recommendation_id FROM investment_decision_record WHERE id = %s", (out.decision_id,))
        assert str(cur.fetchone()[0]) == str(rec.recommendation_id)
    prov = nextaction.provenance(migrated, rec.recommendation_id)
    assert prov["opportunity_id"] == opp
    assert aid in [a["assumption_id"] for a in prov["considered_assumptions"]]
    assert prov["selected_validation_test_id"] == tid


def test_build_decision_provenance_and_claim_source(migrated):
    vid = g.venture(migrated, "e-prov-b")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    cid, obs = g.supported_claim(migrated, vid, opp)
    g.make_candidate(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    g.result(migrated, tid, rkey="rw", score=2, wtp="SIGNED_COMMITMENT")
    g.result(migrated, tid, rkey="ra", score=2, measurement="LANDING_CONVERSION")
    rec = _recommend(migrated, vid, opp)
    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "BUILD"
    # Claim -> SUPPORTS -> Observation -> Source Receipt is traversable.
    cp = claims.provenance(migrated, cid)
    assert cp["state"] == "SUPPORTED"
    assert cp["paths"] and cp["paths"][0]["source_locator"] is not None


def test_new_evidence_does_not_rewrite_old_recommendation(migrated):
    vid = g.venture(migrated, "e-hist")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    t1 = _recommend(migrated, vid, opp, key="r1")  # VALIDATE (unresolved)
    assert t1.action_type == "VALIDATE"
    g.result(migrated, tid, rkey="r", score=2)  # now PASS
    t2 = _recommend(migrated, vid, opp, key="r2")
    # t1's basis is frozen: it still records the original (empty) result state.
    assert nextaction.provenance(migrated, t1.recommendation_id)["considered_results"] == []
    assert len(nextaction.provenance(migrated, t2.recommendation_id)["considered_results"]) == 1


# ==========================================================================
# Anti-hindsight, contradiction preservation, governance-vs-market planes.
# ==========================================================================
def test_precommitted_criteria_are_immutable_after_result(migrated):
    vid = g.venture(migrated, "e-anti")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid, kill=True)
    g.result(migrated, tid, rkey="r", score=0)  # FAIL under the precommitted criteria
    # Rewriting the success or kill criterion after the fact is rejected by the DB.
    import psycopg
    for col, val in (("success_threshold", 99), ("kill_threshold", -1)):
        with pytest.raises(psycopg.errors.RaiseException):
            with migrated.cursor() as cur:
                cur.execute(f"UPDATE validation_test SET {col} = %s WHERE id = %s", (val, tid))
    # The allocator still sees the original decisive kill.
    assert _recommend(migrated, vid, opp).action_type == "KILL"


def test_contradictions_preserved_and_not_cherry_picked(migrated):
    vid = g.venture(migrated, "e-contra")
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    tid = g.discriminating_test(migrated, vid, opp, aid)
    g.result(migrated, tid, rkey="rp", score=2)   # PASS
    g.result(migrated, tid, rkey="ri", score=0)   # INCONCLUSIVE
    rec = _recommend(migrated, vid, opp)
    assert rec.action_type != "BUILD"  # cannot select only the positive side
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM validation_result WHERE validation_test_id = %s", (tid,))
        assert cur.fetchone()[0] == 2  # nothing overwritten


def test_governance_denial_is_not_market_evidence(migrated):
    vid = g.venture(migrated, "e-plane", autonomy=1)  # no budget -> DENY
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    cid, _obs = g.supported_claim(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    claim_state_before = claims.claim_state(migrated, cid)
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1)
    assert out.policy_decision == "DENY"
    # Denial creates no validation result and does not touch the market evidence plane.
    assert g.count(migrated, "validation_result", vid) == 0
    assert claims.claim_state(migrated, cid) == claim_state_before
    assert opportunities.get_status(migrated, opp) == "DRAFT"


# ==========================================================================
# Idempotency & failure atomicity.
# ==========================================================================
def test_commit_idempotent_end_to_end(migrated):
    vid = g.venture(migrated, "e-idem", autonomy=1, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    rec = _recommend(migrated, vid, opp)
    first = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=300, required_autonomy=1)
    second = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=300, required_autonomy=1)
    assert second.created is False and second.decision_id == first.decision_id
    assert second.resulting_action_id == first.resulting_action_id
    assert second.policy_decision == first.policy_decision
    assert g.count(migrated, "investment_decision_record", vid) == 1
    assert g.count(migrated, "action_request", vid) == 1
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM policy_decision WHERE action_request_id = %s", (first.resulting_action_id,))
        assert cur.fetchone()[0] == 1  # no duplicate policy decision


def test_atomicity_spend_over_max_leaves_no_partial_rows(migrated):
    from aidan_core.errors import ConsequentialSpendError

    vid = g.venture(migrated, "e-atom", autonomy=1, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=500)
    rec = _recommend(migrated, vid, opp)
    with pytest.raises(ConsequentialSpendError):
        commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=600, required_autonomy=1)
    # No decision, no orphan ActionRequest, no policy decision.
    assert g.count(migrated, "investment_decision_record", vid) == 0
    assert g.count(migrated, "action_request", vid) == 0
    assert g.global_count(migrated, "policy_decision") == 0


# ==========================================================================
# Security / authority: Gate 3 helpers never touch governed state directly.
# ==========================================================================
def test_gate3_flow_does_not_mutate_governed_state(migrated):
    vid = g.venture(migrated, "e-auth", autonomy=1, grant=1000)
    opp = g.opportunity(migrated, vid)
    aid = g.critical_assumption(migrated, vid, opp)
    g.discriminating_test(migrated, vid, opp, aid, max_spend=1000)
    mandate_before = ventures.get_current_mandate_version(migrated, vid)
    rec = _recommend(migrated, vid, opp)
    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=200, required_autonomy=1)
    # No self-approval, no execution success, no proof, no capital movement, no lifecycle move.
    assert out.policy_decision == "ALLOW"
    from aidan_core import approvals  # noqa: F401  (imported to assert no approval row exists)
    assert g.global_count(migrated, "approval") == 0  # ALLOW opens none
    assert execution.get_status(migrated, out.resulting_action_id) == "PENDING"
    with migrated.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account WHERE venture_id = %s", (vid,))
        from decimal import Decimal
        assert cur.fetchone() == (Decimal("0.0000"), Decimal("0.0000"))
    assert ventures.get_current_mandate_version(migrated, vid) == mandate_before
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
