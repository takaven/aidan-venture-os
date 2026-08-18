"""Gate 3 Slice 2 — highest-value next-action allocator (reasoning only)."""
from __future__ import annotations

import pytest

from aidan_core import nextaction, validation, ventures
from aidan_core.errors import IdempotencyConflictError
from aidan_core.research import assumptions, opportunities

from conftest import full_kill_case, research_claim


def _opp(conn, vid, key="o1"):
    return opportunities.create_opportunity(conn, vid, opportunity_key=key, buyer_hypothesis="B",
                                             problem_hypothesis="P", critical_unknown="U").opportunity_id


def _assume(conn, vid, opp, *, key="a1", importance="CRITICAL"):
    aid = assumptions.create_assumption(conn, vid, proposition="p", assumption_key=key, importance=importance,
                                        confidence="LOW", consequence_if_false="c", cheapest_test="t").assumption_id
    opportunities.link_assumption(conn, opportunity_id=opp, assumption_id=aid)
    return aid


def _test(conn, vid, opp, aid, *, tkey="t1", hkey="h1", structured=True, max_spend=None, kill=False):
    hid = validation.create_hypothesis(conn, vid, opportunity_id=opp, statement="s", hypothesis_key=hkey, assumption_id=aid).hypothesis_id
    kw = dict(validation_hypothesis_id=hid, test_key=tkey, test_type="INTERVIEW", method="m",
              success_criterion="score>=1", evidence_required="notes", max_spend=max_spend)
    if structured:
        kw.update(success_metric="score", success_comparator="GTE", success_threshold=1)
    if kill:
        kw.update(kill_metric="score", kill_comparator="LT", kill_threshold=1)
    return validation.create_test(conn, vid, **kw).test_id


def _result(conn, vid, tid, *, rkey, score, wtp=None, measurement=None):
    return validation.record_result(conn, validation_test_id=tid, result_key=rkey, observed_value={"score": score},
                                    wtp_modality=wtp, measurement_kind=measurement)


def _candidate(conn, vid, opp):
    cid, _ = research_claim(conn, vid, key="rc", stance="SUPPORTS")
    opportunities.link_claim(conn, opportunity_id=opp, claim_id=cid)
    full_kill_case(conn, opp)
    opportunities.finalize_candidate(conn, opp)


def _no_side_effects(cur, vid):
    for table in ("investment_decision_record", "action_request", "capital_entry", "policy_decision", "proof_receipt"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0, table


# --------------------------------------------------------------------------
def test_research_more(migrated):
    vid = ventures.create_venture(migrated, slug="na-rm")
    opp = _opp(migrated, vid)
    _assume(migrated, vid, opp)  # CRITICAL, no validation test
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "RESEARCH_MORE" and r.reason_code == "INSUFFICIENT_EVIDENCE"
    with migrated.cursor() as cur:
        _no_side_effects(cur, vid)


def test_validate_selects_test(migrated):
    vid = ventures.create_venture(migrated, slug="na-val")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid)  # structured, no result yet
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "VALIDATE" and r.reason_code == "CRITICAL_ASSUMPTION_UNRESOLVED"
    assert nextaction.provenance(migrated, r.recommendation_id)["selected_validation_test_id"] == tid


def test_test_selection_prefers_discriminating_and_cheapest(migrated):
    vid = ventures.create_venture(migrated, slug="na-sel")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid, tkey="A", hkey="hA", structured=False)          # non-discriminating
    b = _test(migrated, vid, opp, aid, tkey="B", hkey="hB", structured=True, max_spend=500)
    _test(migrated, vid, opp, aid, tkey="C", hkey="hC", structured=True, max_spend=5000)
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "VALIDATE"
    assert nextaction.provenance(migrated, r.recommendation_id)["selected_validation_test_id"] == b  # cheapest discriminating


def test_failed_kill_criterion_recommends_kill(migrated):
    vid = ventures.create_venture(migrated, slug="na-kill")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid, kill=True)
    res = _result(migrated, vid, tid, rkey="r", score=0)  # score<1 -> FAIL
    assert res.outcome == "FAIL"
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "KILL" and r.reason_code == "KILL_CRITERION_TRIGGERED"
    with migrated.cursor() as cur:
        _no_side_effects(cur, vid)


def test_contradictory_no_kill_is_not_build(migrated):
    vid = ventures.create_venture(migrated, slug="na-contra")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp, importance="MEDIUM")  # MEDIUM -> rule-3 contradiction path
    tid = _test(migrated, vid, opp, aid)  # success score>=1, no kill
    _result(migrated, vid, tid, rkey="rp", score=2)   # PASS
    _result(migrated, vid, tid, rkey="ri", score=0)   # INCONCLUSIVE (no kill)
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type in ("VALIDATE", "HOLD") and r.action_type != "BUILD"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM validation_result WHERE validation_test_id = %s", (tid,))
        assert cur.fetchone()[0] == 2  # both preserved


def test_weak_wtp_not_build(migrated):
    vid = ventures.create_venture(migrated, slug="na-wtp")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)  # CRITICAL WTP assumption
    tid = _test(migrated, vid, opp, aid)
    _result(migrated, vid, tid, rkey="r", score=0, wtp="STATED_INTEREST")  # unmet -> INCONCLUSIVE
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type != "BUILD"


def test_strong_wtp_but_other_unresolved_not_build(migrated):
    vid = ventures.create_venture(migrated, slug="na-wtp2")
    opp = _opp(migrated, vid)
    wtp = _assume(migrated, vid, opp, key="wtp", importance="CRITICAL")
    other = _assume(migrated, vid, opp, key="other", importance="CRITICAL")
    twtp = _test(migrated, vid, opp, wtp, tkey="twtp", hkey="hwtp")
    _result(migrated, vid, twtp, rkey="rw", score=2, wtp="SIGNED_COMMITMENT")  # PASS resolves WTP
    _test(migrated, vid, opp, other, tkey="tother", hkey="hother")  # other still unresolved (no result)
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "VALIDATE"  # the other critical assumption still needs validation


def test_strong_acquisition_weak_wtp_not_build(migrated):
    vid = ventures.create_venture(migrated, slug="na-acq")
    opp = _opp(migrated, vid)
    acq = _assume(migrated, vid, opp, key="acq", importance="CRITICAL")
    wtp = _assume(migrated, vid, opp, key="wtp", importance="CRITICAL")
    tacq = _test(migrated, vid, opp, acq, tkey="tacq", hkey="hacq")
    _result(migrated, vid, tacq, rkey="ra", score=2, measurement="LANDING_CONVERSION")  # PASS acquisition
    _test(migrated, vid, opp, wtp, tkey="twtp", hkey="hwtp")  # wtp unresolved
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type != "BUILD"


def test_build_recommendation_is_not_authorization(migrated):
    vid = ventures.create_venture(migrated, slug="na-build")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)          # CRITICAL, resolved below
    _candidate(migrated, vid, opp)             # opportunity -> CANDIDATE
    tid = _test(migrated, vid, opp, aid)
    _result(migrated, vid, tid, rkey="r", score=2)  # PASS resolves the critical assumption
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "BUILD" and r.reason_code == "BUILD_CONSIDERATION_READY"
    # Recommendation is NOT authorization: nothing consequential happened.
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    assert opportunities.get_status(migrated, opp) == "CANDIDATE"  # unchanged
    with migrated.cursor() as cur:
        _no_side_effects(cur, vid)


def test_hold_when_no_high_value_action(migrated):
    vid = ventures.create_venture(migrated, slug="na-hold")
    opp = _opp(migrated, vid)
    _assume(migrated, vid, opp, importance="LOW")  # low importance, no validation, not candidate
    r = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert r.action_type == "HOLD" and r.reason_code == "NO_HIGH_VALUE_ACTION_NOW"


def test_historical_recommendations_are_append_only(migrated):
    vid = ventures.create_venture(migrated, slug="na-hist")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid)
    t1 = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")  # VALIDATE (unresolved)
    assert t1.action_type == "VALIDATE"
    _result(migrated, vid, tid, rkey="r", score=2)  # now PASS
    t2 = nextaction.recommend(migrated, vid, opp, recommendation_key="r2")  # new recommendation
    # T1 preserved unchanged and still links its original (empty) result state.
    assert nextaction.get_recommendation(migrated, t1.recommendation_id)[3] == "VALIDATE"
    assert nextaction.provenance(migrated, t1.recommendation_id)["considered_results"] == []
    assert len(nextaction.provenance(migrated, t2.recommendation_id)["considered_results"]) == 1
    assert nextaction.latest_recommendation(migrated, opp)[0] == t2.recommendation_id


def test_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="na-idem")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid)
    a = nextaction.recommend(migrated, vid, opp, recommendation_key="k")
    b = nextaction.recommend(migrated, vid, opp, recommendation_key="k")
    assert b.created is False and b.recommendation_id == a.recommendation_id
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_event WHERE venture_id = %s AND event_type = 'nextaction.recommended'", (vid,))
        assert cur.fetchone()[0] == 1  # no duplicate audit
    # changed input state under the same key conflicts.
    _result(migrated, vid, validation.create_test(migrated, vid, validation_hypothesis_id=validation.create_hypothesis(
        migrated, vid, opportunity_id=opp, statement="s2", hypothesis_key="h2", assumption_id=aid).hypothesis_id,
        test_key="t2", test_type="INTERVIEW", method="m", success_criterion="c", evidence_required="e",
        success_metric="score", success_comparator="GTE", success_threshold=1).test_id, rkey="rr", score=2)
    with pytest.raises(IdempotencyConflictError):
        nextaction.recommend(migrated, vid, opp, recommendation_key="k")


def test_allocator_has_no_authority(migrated):
    vid = ventures.create_venture(migrated, slug="na-auth")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid)
    nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    with migrated.cursor() as cur:
        for table in ("policy_decision", "kill_switch", "proof_receipt", "investment_decision_record",
                      "capital_entry", "action_request"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table
