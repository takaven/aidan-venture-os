"""Composable canonical-state builders for the Gate 3 end-to-end evals.

These helpers construct EXPLICIT canonical state (venture, mandate, opportunity,
claims/evidence, assumptions, kill case, validation tests/results, budget) using
the real kernel APIs — never by injecting an expected decision. Each helper is
named around the invariant it establishes so an eval reads as the state it sets
up, not an opaque builder. Production code never imports this module.

The end-to-end Gate 3 flow the evals exercise is just two public calls:
``nextaction.recommend(...)`` then ``commitment.commit_recommendation(...)``.
"""
from __future__ import annotations

from aidan_core import budget, ventures
from aidan_core.research import assumptions, opportunities
from aidan_core import validation

from conftest import full_kill_case, research_claim


def venture(conn, slug, *, autonomy=0, grant=None, currency="USD"):
    """A canonical venture with a mandate version, optionally funded."""
    vid = ventures.create_venture(conn, slug=slug, autonomy_level=autonomy)
    ventures.append_mandate_version(conn, vid, content_hash="m1")
    if grant is not None:
        budget.grant_budget(conn, vid, amount=grant, currency=currency)
    return vid


def opportunity(conn, vid, *, key="o1"):
    """A DRAFT opportunity with the hypotheses finalize_candidate requires."""
    return opportunities.create_opportunity(
        conn, vid, opportunity_key=key, buyer_hypothesis="B",
        problem_hypothesis="P", critical_unknown="U",
    ).opportunity_id


def critical_assumption(conn, vid, opp, *, key="a1", importance="CRITICAL"):
    """An assumption linked to the opportunity (CRITICAL unless overridden)."""
    aid = assumptions.create_assumption(
        conn, vid, proposition="p", assumption_key=key, importance=importance,
        confidence="LOW", consequence_if_false="c", cheapest_test="t",
    ).assumption_id
    opportunities.link_assumption(conn, opportunity_id=opp, assumption_id=aid)
    return aid


def discriminating_test(conn, vid, opp, aid, *, tkey="t1", hkey="h1", max_spend=None, kill=False):
    """A structured (PASS/FAIL-derivable) validation test for an assumption."""
    hid = validation.create_hypothesis(
        conn, vid, opportunity_id=opp, statement="s", hypothesis_key=hkey, assumption_id=aid
    ).hypothesis_id
    kw = dict(
        validation_hypothesis_id=hid, test_key=tkey, test_type="INTERVIEW", method="m",
        success_criterion="score>=1", evidence_required="notes", max_spend=max_spend,
        success_metric="score", success_comparator="GTE", success_threshold=1,
    )
    if kill:
        kw.update(kill_metric="score", kill_comparator="LT", kill_threshold=1)
    return validation.create_test(conn, vid, **kw).test_id


def nondiscriminating_test(conn, vid, opp, aid, *, tkey="tn", hkey="hn", max_spend=None):
    """A test with no structured criterion — cannot deterministically discriminate."""
    hid = validation.create_hypothesis(
        conn, vid, opportunity_id=opp, statement="s", hypothesis_key=hkey, assumption_id=aid
    ).hypothesis_id
    return validation.create_test(
        conn, vid, validation_hypothesis_id=hid, test_key=tkey, test_type="INTERVIEW",
        method="m", success_criterion="subjective", evidence_required="notes", max_spend=max_spend,
    ).test_id


def result(conn, tid, *, rkey, score, wtp=None, measurement=None, interpretation=None):
    """Record an observed result; outcome is DERIVED from the test's criteria."""
    return validation.record_result(
        conn, validation_test_id=tid, result_key=rkey, observed_value={"score": score},
        wtp_modality=wtp, measurement_kind=measurement, interpretation=interpretation,
    )


def supported_claim(conn, vid, opp, *, key="rc"):
    """Link a Claim with positive provenance (SUPPORTS -> Observation -> Source)."""
    cid, obs = research_claim(conn, vid, key=key, stance="SUPPORTS")
    opportunities.link_claim(conn, opportunity_id=opp, claim_id=cid)
    return cid, obs


def make_candidate(conn, vid, opp):
    """Finalize DRAFT -> CANDIDATE (requires linked claim/assumption + full kill case)."""
    full_kill_case(conn, opp)
    return opportunities.finalize_candidate(conn, opp)


def build_ready(conn, vid, opp, *, aid=None):
    """A genuinely BUILD-ready opportunity: CANDIDATE, SUPPORTED claim, complete
    Kill Case, one CRITICAL assumption resolved by BOTH a WTP-context PASS and an
    acquisition-context PASS. Returns (assumption_id, test_id)."""
    if aid is None:
        aid = critical_assumption(conn, vid, opp)
    supported_claim(conn, vid, opp)
    make_candidate(conn, vid, opp)
    tid = discriminating_test(conn, vid, opp, aid)
    result(conn, tid, rkey="rw", score=2, wtp="SIGNED_COMMITMENT")        # WTP-context PASS
    result(conn, tid, rkey="ra", score=2, measurement="LANDING_CONVERSION")  # acquisition PASS
    return aid, tid


def count(conn, table, vid):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE venture_id = %s", (vid,))
        return cur.fetchone()[0]


def global_count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def policy_reason(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT decision, reason FROM policy_decision WHERE action_request_id = %s", (action_id,))
        return cur.fetchone()


def assert_no_execution(conn, action_id):
    """No Gate 3 flow may execute, prove, or manufacture ActionRequest success."""
    from aidan_core import execution

    assert execution.get_status(conn, action_id) != "SUCCEEDED"
    assert global_count(conn, "execution_attempt") == 0
    assert global_count(conn, "proof_receipt") == 0
