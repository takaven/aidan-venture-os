"""Gate 3 Slice 3 — governed conversion of a recommendation into an investment
decision (+ optional Gate 1 ActionRequest), with staleness, compatibility, an
independent BUILD re-gate, the VALIDATE spend boundary, and submission of any
consequential ActionRequest into the existing Gate 1 policy boundary
(ALLOW / REQUIRE_APPROVAL / DENY), without approving or executing.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from aidan_core import approvals, budget, commitment, execution, killswitch, nextaction, validation, ventures
from aidan_core.errors import (
    BuildGateNotSatisfiedError,
    ConsequentialSpendError,
    RecommendationNotConvertibleError,
    StaleRecommendationError,
)
from aidan_core.research import assumptions, opportunities

from conftest import full_kill_case, research_claim


# --------------------------------------------------------------------------
# State builders (mirroring the Slice 2 allocator fixtures).
# --------------------------------------------------------------------------
def _opp(conn, vid, key="o1"):
    return opportunities.create_opportunity(
        conn, vid, opportunity_key=key, buyer_hypothesis="B", problem_hypothesis="P", critical_unknown="U"
    ).opportunity_id


def _assume(conn, vid, opp, *, key="a1", importance="CRITICAL"):
    aid = assumptions.create_assumption(
        conn, vid, proposition="p", assumption_key=key, importance=importance,
        confidence="LOW", consequence_if_false="c", cheapest_test="t",
    ).assumption_id
    opportunities.link_assumption(conn, opportunity_id=opp, assumption_id=aid)
    return aid


def _test(conn, vid, opp, aid, *, tkey="t1", hkey="h1", structured=True, max_spend=None, kill=False):
    hid = validation.create_hypothesis(
        conn, vid, opportunity_id=opp, statement="s", hypothesis_key=hkey, assumption_id=aid
    ).hypothesis_id
    kw = dict(validation_hypothesis_id=hid, test_key=tkey, test_type="INTERVIEW", method="m",
              success_criterion="score>=1", evidence_required="notes", max_spend=max_spend)
    if structured:
        kw.update(success_metric="score", success_comparator="GTE", success_threshold=1)
    if kill:
        kw.update(kill_metric="score", kill_comparator="LT", kill_threshold=1)
    return validation.create_test(conn, vid, **kw).test_id


def _result(conn, tid, *, rkey, score, wtp=None, measurement=None):
    return validation.record_result(
        conn, validation_test_id=tid, result_key=rkey, observed_value={"score": score},
        wtp_modality=wtp, measurement_kind=measurement,
    )


def _candidate(conn, vid, opp):
    cid, _ = research_claim(conn, vid, key="rc", stance="SUPPORTS")
    opportunities.link_claim(conn, opportunity_id=opp, claim_id=cid)
    full_kill_case(conn, opp)
    opportunities.finalize_candidate(conn, opp)


def _build_ready(conn, vid, opp):
    """A state the allocator recommends BUILD for AND that satisfies the re-gate:
    CANDIDATE, SUPPORTED claim, complete kill case, one CRITICAL assumption
    resolved by both a WTP-context PASS and an acquisition-context PASS."""
    aid = _assume(conn, vid, opp)
    _candidate(conn, vid, opp)
    tid = _test(conn, vid, opp, aid)
    _result(conn, tid, rkey="rw", score=2, wtp="SIGNED_COMMITMENT")     # WTP-context PASS
    _result(conn, tid, rkey="ra", score=2, measurement="LANDING_CONVERSION")  # acquisition PASS
    return aid, tid


def _count(conn, table, vid):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE venture_id = %s", (vid,))
        return cur.fetchone()[0]


def _validate_ready(conn, vid, opp, *, max_spend=1000):
    """A state the allocator recommends VALIDATE for, with a bounded test."""
    aid = _assume(conn, vid, opp)
    tid = _test(conn, vid, opp, aid, max_spend=max_spend)
    return aid, tid


def _no_execution_side_effects(conn, action_id):
    """A governed decision + policy evaluation must not execute or prove."""
    assert execution.get_status(conn, action_id) != "SUCCEEDED"
    with conn.cursor() as cur:
        for table in ("proof_receipt", "execution_attempt"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table


# --------------------------------------------------------------------------
# Standard 1:1 mappings.
# --------------------------------------------------------------------------
def test_validate_recommendation_becomes_validate_decision(migrated):
    vid = ventures.create_venture(migrated, slug="c-val")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid, max_spend=500)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "VALIDATE"

    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "VALIDATE" and out.created and out.resulting_action_id is None
    with migrated.cursor() as cur:
        cur.execute("SELECT decision, source_recommendation_id FROM investment_decision_record WHERE id = %s", (out.decision_id,))
        dec, src = cur.fetchone()
        assert dec == "VALIDATE" and str(src) == str(rec.recommendation_id)


def test_kill_recommendation_becomes_kill_decision(migrated):
    vid = ventures.create_venture(migrated, slug="c-kill")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid, kill=True)
    assert _result(migrated, tid, rkey="r", score=0).outcome == "FAIL"
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "KILL"

    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "KILL" and out.resulting_action_id is None


def test_hold_recommendation_becomes_hold_decision(migrated):
    vid = ventures.create_venture(migrated, slug="c-hold")
    opp = _opp(migrated, vid)
    _assume(migrated, vid, opp, importance="LOW")
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "HOLD"
    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "HOLD" and out.resulting_action_id is None


def test_build_recommendation_becomes_build_decision_when_regate_passes(migrated):
    vid = ventures.create_venture(migrated, slug="c-build")
    opp = _opp(migrated, vid)
    _build_ready(migrated, vid, opp)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "BUILD"

    out = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert out.decision == "BUILD"
    # No honest amount supplied -> a BUILD decision without a BUILD ActionRequest.
    assert out.resulting_action_id is None
    assert _count(migrated, "action_request", vid) == 0


# --------------------------------------------------------------------------
# RESEARCH_MORE maps to NO investment decision (and no enum extension).
# --------------------------------------------------------------------------
def test_research_more_is_not_convertible(migrated):
    vid = ventures.create_venture(migrated, slug="c-rm")
    opp = _opp(migrated, vid)
    _assume(migrated, vid, opp)  # CRITICAL, no discriminating test -> RESEARCH_MORE
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "RESEARCH_MORE"

    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert _count(migrated, "investment_decision_record", vid) == 0
    # RESEARCH_MORE was NOT added to the investment_decision enum.
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'investment_decision'"
        )
        labels = {r[0] for r in cur.fetchall()}
    assert "RESEARCH_MORE" not in labels
    assert labels == {"VALIDATE", "BUILD", "IMPROVE", "MARKET", "SCALE", "HOLD", "KILL", "DO_NOTHING"}


# --------------------------------------------------------------------------
# Incompatible explicit mappings are refused.
# --------------------------------------------------------------------------
def test_kill_cannot_be_committed_as_build(migrated):
    vid = ventures.create_venture(migrated, slug="c-k2b")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid, kill=True)
    _result(migrated, tid, rkey="r", score=0)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "KILL"
    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id, decision="BUILD")
    assert _count(migrated, "investment_decision_record", vid) == 0


def test_build_cannot_be_committed_as_scale(migrated):
    vid = ventures.create_venture(migrated, slug="c-b2s")
    opp = _opp(migrated, vid)
    _build_ready(migrated, vid, opp)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "BUILD"
    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id, decision="SCALE")
    assert _count(migrated, "investment_decision_record", vid) == 0


# --------------------------------------------------------------------------
# Stale recommendation protection (load-bearing).
# --------------------------------------------------------------------------
def test_stale_build_after_decisive_fail_cannot_commit(migrated):
    vid = ventures.create_venture(migrated, slug="c-stale")
    opp = _opp(migrated, vid)
    aid, _tid = _build_ready(migrated, vid, opp)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "BUILD"

    # A later decisive validation FAIL changes the canonical basis.
    ktid = _test(migrated, vid, opp, aid, tkey="tk", hkey="hk", kill=True)
    assert _result(migrated, ktid, rkey="rk", score=0).outcome == "FAIL"

    with pytest.raises(StaleRecommendationError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)
    # No BUILD decision and no resulting ActionRequest were created from the stale rec.
    assert _count(migrated, "investment_decision_record", vid) == 0
    assert _count(migrated, "action_request", vid) == 0
    # The prior recommendation is unchanged (never mutated).
    assert nextaction.get_recommendation(migrated, rec.recommendation_id)[3] == "BUILD"


# --------------------------------------------------------------------------
# BUILD is independently re-gated (not trusted because it was recommended).
# --------------------------------------------------------------------------
def test_build_refused_when_acquisition_validation_absent(migrated):
    vid = ventures.create_venture(migrated, slug="c-noacq")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _candidate(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid)
    _result(migrated, tid, rkey="rw", score=2, wtp="SIGNED_COMMITMENT")  # WTP only; no acquisition PASS
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "BUILD"  # allocator is satisfied by structural readiness

    with pytest.raises(BuildGateNotSatisfiedError) as ei:
        commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert "acquisition" in str(ei.value)
    assert _count(migrated, "investment_decision_record", vid) == 0
    assert _count(migrated, "action_request", vid) == 0  # no orphan action from a refused decision


def test_build_with_bounded_amount_creates_pending_actionrequest(migrated):
    vid = ventures.create_venture(migrated, slug="c-buildamt")
    budget.grant_budget(migrated, vid, amount=1000, currency="USD")
    opp = _opp(migrated, vid)
    _build_ready(migrated, vid, opp)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "BUILD"

    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=250)
    assert out.decision == "BUILD" and out.resulting_action_id is not None
    # The consequential BUILD action entered the Gate 1 policy boundary.
    assert out.policy_decision == "ALLOW" and out.policy_decision_id is not None
    assert execution.get_status(migrated, out.resulting_action_id) == "PENDING"
    with migrated.cursor() as cur:
        cur.execute("SELECT requested_amount FROM action_request WHERE id = %s", (out.resulting_action_id,))
        assert cur.fetchone()[0] == Decimal("250.0000")
        # Policy evaluated (one decision); nothing reserved, executed, or proven.
        cur.execute("SELECT count(*) FROM policy_decision WHERE action_request_id = %s", (out.resulting_action_id,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account WHERE venture_id = %s", (vid,))
        assert cur.fetchone() == (Decimal("0.0000"), Decimal("0.0000"))
        for table in ("proof_receipt", "execution_attempt"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    assert opportunities.get_status(migrated, opp) == "CANDIDATE"


# --------------------------------------------------------------------------
# VALIDATE spend boundary.
# --------------------------------------------------------------------------
def test_validate_spend_within_max_spend_creates_action(migrated):
    vid = ventures.create_venture(migrated, slug="c-spendok")
    budget.grant_budget(migrated, vid, amount=1000, currency="USD")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    tid = _test(migrated, vid, opp, aid, max_spend=500)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "VALIDATE"
    assert nextaction.provenance(migrated, rec.recommendation_id)["selected_validation_test_id"] == tid

    out = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=400)
    assert out.decision == "VALIDATE" and out.resulting_action_id is not None
    assert out.policy_decision == "ALLOW"
    with migrated.cursor() as cur:
        cur.execute("SELECT status, requested_amount, payload FROM action_request WHERE id = %s", (out.resulting_action_id,))
        status, amount, payload = cur.fetchone()
    assert status == "PENDING" and amount == Decimal("400.0000")
    assert str(payload["validation_test_id"]) == str(tid)


def test_validate_spend_exceeding_max_spend_is_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="c-spendover")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid, max_spend=500)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "VALIDATE"
    with pytest.raises(ConsequentialSpendError):
        commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=600)
    assert _count(migrated, "investment_decision_record", vid) == 0
    assert _count(migrated, "action_request", vid) == 0


def test_validate_spend_without_precommitted_bound_is_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="c-spendnobound")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid, max_spend=None)  # discriminating but no precommitted cap
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "VALIDATE"
    with pytest.raises(ConsequentialSpendError):
        commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=10)


# --------------------------------------------------------------------------
# Authority boundary + idempotency + atomicity.
# --------------------------------------------------------------------------
def test_decision_does_not_touch_lifecycle_or_execution(migrated):
    vid = ventures.create_venture(migrated, slug="c-auth")
    opp = _opp(migrated, vid)
    _build_ready(migrated, vid, opp)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    with migrated.cursor() as cur:
        for table in ("policy_decision", "proof_receipt", "capital_entry", "execution_attempt", "approval"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table


def test_commit_is_idempotent_per_recommendation(migrated):
    vid = ventures.create_venture(migrated, slug="c-idem")
    opp = _opp(migrated, vid)
    aid = _assume(migrated, vid, opp)
    _test(migrated, vid, opp, aid, max_spend=500)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")

    first = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=100)
    second = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=100)
    assert second.created is False
    assert second.decision_id == first.decision_id
    assert second.resulting_action_id == first.resulting_action_id
    # Same canonical policy outcome on retry; no duplicate decision/action/policy.
    assert second.policy_decision == first.policy_decision
    assert _count(migrated, "investment_decision_record", vid) == 1
    assert _count(migrated, "action_request", vid) == 1
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM policy_decision WHERE action_request_id = %s", (first.resulting_action_id,))
        assert cur.fetchone()[0] == 1  # exactly one policy decision, not two
        cur.execute(
            "SELECT count(*) FROM audit_event WHERE action_id = %s AND event_type = 'policy.evaluated'",
            (first.resulting_action_id,),
        )
        assert cur.fetchone()[0] == 1  # no duplicate policy audit


def test_missing_recommendation_raises(migrated):
    from aidan_core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        commitment.commit_recommendation(migrated, "00000000-0000-0000-0000-000000000000")


# --------------------------------------------------------------------------
# Gate 1 policy boundary: a consequential VALIDATE action must enter the existing
# ActionRequest -> Policy path and yield ALLOW / REQUIRE_APPROVAL / DENY, without
# approving, executing, moving capital, or manufacturing success.
# --------------------------------------------------------------------------
def test_policy_allow(migrated):
    vid = ventures.create_venture(migrated, slug="c-allow", autonomy_level=1)
    budget.grant_budget(migrated, vid, amount=1000, currency="USD")
    opp = _opp(migrated, vid)
    _validate_ready(migrated, vid, opp, max_spend=1000)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "VALIDATE"

    out = commitment.commit_recommendation(
        migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1
    )
    assert out.decision == "VALIDATE" and out.policy_decision == "ALLOW"
    assert out.approval_id is None
    # ALLOW does not execute: status stays PENDING, no approval opened.
    assert execution.get_status(migrated, out.resulting_action_id) == "PENDING"
    _no_execution_side_effects(migrated, out.resulting_action_id)
    with migrated.cursor() as cur:
        cur.execute("SELECT decision, reason FROM policy_decision WHERE action_request_id = %s", (out.resulting_action_id,))
        assert cur.fetchone() == ("ALLOW", "ALLOWED")
        cur.execute("SELECT count(*) FROM approval WHERE action_request_id = %s", (out.resulting_action_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account WHERE venture_id = %s", (vid,))
        assert cur.fetchone() == (Decimal("0.0000"), Decimal("0.0000"))  # ALLOW != reservation
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"


def test_policy_require_approval_not_auto_granted(migrated):
    vid = ventures.create_venture(migrated, slug="c-appr", autonomy_level=0)
    budget.grant_budget(migrated, vid, amount=1000, currency="USD")
    opp = _opp(migrated, vid)
    _validate_ready(migrated, vid, opp, max_spend=1000)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")

    # required_autonomy above the venture's autonomy -> REQUIRE_APPROVAL.
    out = commitment.commit_recommendation(
        migrated, rec.recommendation_id, requested_amount=400, required_autonomy=2
    )
    assert out.policy_decision == "REQUIRE_APPROVAL"
    assert out.approval_id is not None
    # The approval is opened PENDING and NOT automatically granted.
    assert approvals.get_approval(migrated, out.approval_id)[3] == "PENDING"
    assert execution.get_status(migrated, out.resulting_action_id) == "AWAITING_APPROVAL"
    _no_execution_side_effects(migrated, out.resulting_action_id)
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"


def test_policy_deny_kill_switch(migrated):
    vid = ventures.create_venture(migrated, slug="c-deny-ks", autonomy_level=1)
    budget.grant_budget(migrated, vid, amount=1000, currency="USD")
    opp = _opp(migrated, vid)
    _validate_ready(migrated, vid, opp, max_spend=1000)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    killswitch.engage_global(migrated, engaged_by="op")

    out = commitment.commit_recommendation(
        migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1
    )
    # Kill switch has highest precedence -> DENY. Decision + action still recorded.
    assert out.decision == "VALIDATE" and out.policy_decision == "DENY"
    with migrated.cursor() as cur:
        cur.execute("SELECT reason FROM policy_decision WHERE action_request_id = %s", (out.resulting_action_id,))
        assert cur.fetchone()[0] == "KILL_SWITCH_GLOBAL"
    assert execution.get_status(migrated, out.resulting_action_id) == "PENDING"
    _no_execution_side_effects(migrated, out.resulting_action_id)
    # The investment decision remains a separate historical record.
    assert _count(migrated, "investment_decision_record", vid) == 1


def test_policy_deny_insufficient_budget(migrated):
    vid = ventures.create_venture(migrated, slug="c-deny-bud", autonomy_level=1)
    # No budget granted -> available 0 < requested amount.
    opp = _opp(migrated, vid)
    _validate_ready(migrated, vid, opp, max_spend=1000)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")

    out = commitment.commit_recommendation(
        migrated, rec.recommendation_id, requested_amount=400, required_autonomy=1
    )
    assert out.policy_decision == "DENY"
    with migrated.cursor() as cur:
        cur.execute("SELECT reason FROM policy_decision WHERE action_request_id = %s", (out.resulting_action_id,))
        assert cur.fetchone()[0] == "INSUFFICIENT_BUDGET"
    _no_execution_side_effects(migrated, out.resulting_action_id)
    # Governance denial is not market evidence: no validation result fabricated.
    assert _count(migrated, "validation_result", vid) == 0


def test_separation_decision_persists_while_action_awaits_governance(migrated):
    vid = ventures.create_venture(migrated, slug="c-sep", autonomy_level=0)
    budget.grant_budget(migrated, vid, amount=1000, currency="USD")
    opp = _opp(migrated, vid)
    _validate_ready(migrated, vid, opp, max_spend=1000)
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    results_before = _count(migrated, "validation_result", vid)

    out = commitment.commit_recommendation(
        migrated, rec.recommendation_id, requested_amount=400, required_autonomy=2
    )
    # The investment decision exists as a durable record while the action still
    # awaits governance (REQUIRE_APPROVAL, not executed, not succeeded).
    assert _count(migrated, "investment_decision_record", vid) == 1
    assert out.policy_decision == "REQUIRE_APPROVAL"
    assert execution.get_status(migrated, out.resulting_action_id) == "AWAITING_APPROVAL"
    # Policy evaluation did not mutate validation evidence or lifecycle.
    assert _count(migrated, "validation_result", vid) == results_before
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
