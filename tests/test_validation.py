"""Gate 3 Slice 1 — validation hypotheses, precommitted tests, observed results."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import actions, validation, ventures
from aidan_core.errors import IdempotencyConflictError, NotFoundError
from aidan_core.research import assumptions, opportunities


def _opp(conn, vid, key="o1"):
    return opportunities.create_opportunity(conn, vid, opportunity_key=key, buyer_hypothesis="B", problem_hypothesis="P", critical_unknown="U").opportunity_id


def _assume(conn, vid, key="a1"):
    return assumptions.create_assumption(conn, vid, proposition="p", assumption_key=key, importance="HIGH", confidence="LOW", consequence_if_false="c", cheapest_test="t").assumption_id


def _hyp(conn, vid, *, key="h1", opp=None, assumption=None):
    opp = opp or _opp(conn, vid)
    return validation.create_hypothesis(conn, vid, opportunity_id=opp, statement="uncertainty", hypothesis_key=key, assumption_id=assumption).hypothesis_id


def _test(conn, vid, hid, *, key="t1", **kw):
    base = dict(validation_hypothesis_id=hid, test_key=key, test_type="INTERVIEW", method="talk to buyers",
                success_criterion="≥3 of 10 confirm", evidence_required="interview notes")
    base.update(kw)
    return validation.create_test(conn, vid, **base).test_id


# --------------------------------------------------------------------------
# Hypothesis.
# --------------------------------------------------------------------------
def test_hypothesis_links_and_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="v-h1")
    opp, asm = _opp(migrated, vid), _assume(migrated, vid)
    a = validation.create_hypothesis(migrated, vid, opportunity_id=opp, statement="s", hypothesis_key="k", assumption_id=asm)
    b = validation.create_hypothesis(migrated, vid, opportunity_id=opp, statement="s", hypothesis_key="k", assumption_id=asm)
    assert b.created is False and b.hypothesis_id == a.hypothesis_id
    with pytest.raises(IdempotencyConflictError):
        validation.create_hypothesis(migrated, vid, opportunity_id=opp, statement="DIFFERENT", hypothesis_key="k")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE validation_hypothesis SET statement = 'x' WHERE id = %s", (a.hypothesis_id,))


def test_hypothesis_cross_venture_rejected(migrated):
    a, b = ventures.create_venture(migrated, slug="v-ha"), ventures.create_venture(migrated, slug="v-hb")
    opp_b = _opp(migrated, b)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        validation.create_hypothesis(migrated, a, opportunity_id=opp_b, statement="s", hypothesis_key="k")
    opp_a, asm_b = _opp(migrated, a, key="oa"), _assume(migrated, b, key="ab")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        validation.create_hypothesis(migrated, a, opportunity_id=opp_a, statement="s", hypothesis_key="k2", assumption_id=asm_b)


def test_hypothesis_no_side_effects(migrated):
    vid = ventures.create_venture(migrated, slug="v-hns")
    _hyp(migrated, vid)
    with migrated.cursor() as cur:
        for table in ("action_request", "capital_entry", "investment_decision_record"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------
# Precommitted test (immutable definition; no execution status).
# --------------------------------------------------------------------------
def test_test_requires_hypothesis(migrated):
    vid = ventures.create_venture(migrated, slug="v-treq")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _test(migrated, vid, "00000000-0000-0000-0000-000000000000")


def test_test_definition_immutable_and_idempotent(migrated):
    vid = ventures.create_venture(migrated, slug="v-timm")
    hid = _hyp(migrated, vid)
    tid = _test(migrated, vid, hid, key="t", kill_criterion="0 replies")
    # exact retry converges; changed definition conflicts.
    assert validation.create_test(migrated, vid, validation_hypothesis_id=hid, test_key="t", test_type="INTERVIEW",
                                  method="talk to buyers", success_criterion="≥3 of 10 confirm",
                                  evidence_required="interview notes", kill_criterion="0 replies").created is False
    with pytest.raises(IdempotencyConflictError):
        validation.create_test(migrated, vid, validation_hypothesis_id=hid, test_key="t", test_type="INTERVIEW",
                               method="talk to buyers", success_criterion="CHANGED", evidence_required="interview notes")
    # immutable definition.
    for col in ("success_criterion", "kill_criterion", "method"):
        with pytest.raises(psycopg.errors.RaiseException):
            with migrated.cursor() as cur:
                cur.execute(f"UPDATE validation_test SET {col} = 'x' WHERE id = %s", (tid,))


def test_validation_test_has_no_execution_status(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'validation_test'")
        cols = {r[0] for r in cur.fetchall()}
    assert not (cols & {"status", "state", "executed", "complete", "run_status"})


# --------------------------------------------------------------------------
# ActionRequest linkage (set-once; no spend/approval/execution).
# --------------------------------------------------------------------------
def test_action_link_same_venture_set_once(migrated):
    vid = ventures.create_venture(migrated, slug="v-link")
    hid = _hyp(migrated, vid)
    tid = _test(migrated, vid, hid)
    aid = actions.submit_action_request(migrated, venture_id=vid, action_type="validate", actor="a", idempotency_key="k").action_id
    assert validation.link_action_request(migrated, test_id=tid, action_request_id=aid) is True
    assert validation.link_action_request(migrated, test_id=tid, action_request_id=aid) is False  # idempotent
    aid2 = actions.submit_action_request(migrated, venture_id=vid, action_type="validate", actor="a", idempotency_key="k2").action_id
    with pytest.raises(IdempotencyConflictError):
        validation.link_action_request(migrated, test_id=tid, action_request_id=aid2)
    # No capital reserved, no proof, action still PENDING.
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM capital_entry")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM proof_receipt")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT status FROM action_request WHERE id = %s", (aid,))
        assert cur.fetchone()[0] == "PENDING"


def test_action_link_cross_venture_rejected(migrated):
    a, b = ventures.create_venture(migrated, slug="v-la"), ventures.create_venture(migrated, slug="v-lb")
    tid = _test(migrated, a, _hyp(migrated, a))
    aid_b = actions.submit_action_request(migrated, venture_id=b, action_type="validate", actor="a", idempotency_key="k").action_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        validation.link_action_request(migrated, test_id=tid, action_request_id=aid_b)


# --------------------------------------------------------------------------
# Results.
# --------------------------------------------------------------------------
def test_result_append_and_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="v-res")
    tid = _test(migrated, vid, _hyp(migrated, vid))
    a = validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"replies": 3})
    b = validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"replies": 3})
    assert b.created is False and b.result_id == a.result_id
    with pytest.raises(IdempotencyConflictError):
        validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"replies": 9})
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE validation_result SET outcome = 'PASS' WHERE id = %s", (a.result_id,))


def test_observed_and_interpretation_separated(migrated):
    vid = ventures.create_venture(migrated, slug="v-obsint")
    tid = _test(migrated, vid, _hyp(migrated, vid))
    r = validation.record_result(migrated, validation_test_id=tid, result_key="r",
                                 observed_value={"replies": 1}, interpretation="looks promising")
    row = validation.get_result(migrated, r.result_id)
    assert row[3] == {"replies": 1} and row[4] == "looks promising"  # observed_value vs interpretation


def test_result_no_investment_or_lifecycle(migrated):
    vid = ventures.create_venture(migrated, slug="v-resns")
    tid = _test(migrated, vid, _hyp(migrated, vid))
    validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"x": 1})
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record")
        assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------
# WTP + acquisition typing (separate categorical domains).
# --------------------------------------------------------------------------
def test_wtp_categories_are_categorical(migrated):
    vid = ventures.create_venture(migrated, slug="v-wtp")
    tid = _test(migrated, vid, _hyp(migrated, vid))
    for i, m in enumerate(["STATED_INTEREST", "STATED_WILLINGNESS", "SIGNED_COMMITMENT", "ACTUAL_PAYMENT"]):
        validation.record_result(migrated, validation_test_id=tid, result_key=f"r{i}", observed_value={"n": i}, wtp_modality=m)
    with pytest.raises(ValueError):
        validation.record_result(migrated, validation_test_id=tid, result_key="bad", observed_value={}, wtp_modality="0.7")


def test_acquisition_metric_separate_from_wtp(migrated):
    vid = ventures.create_venture(migrated, slug="v-acq")
    tid = _test(migrated, vid, _hyp(migrated, vid), test_type="OUTREACH")
    r = validation.record_result(migrated, validation_test_id=tid, result_key="r",
                                 observed_value={"sent": 100, "responses": 7}, measurement_kind="OUTREACH_RESPONSE")
    row = validation.get_result(migrated, r.result_id)
    assert row[7] == "OUTREACH_RESPONSE" and row[6] is None  # measurement_kind set, wtp_modality None


# --------------------------------------------------------------------------
# Precommitted criteria immutable after results (anti-hindsight).
# --------------------------------------------------------------------------
def test_criteria_immutable_after_result(migrated):
    vid = ventures.create_venture(migrated, slug="v-precommit")
    tid = _test(migrated, vid, _hyp(migrated, vid), key="t", kill_criterion="0 conversions")
    validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"x": 0})
    for col in ("success_criterion", "kill_criterion"):
        with pytest.raises(psycopg.errors.RaiseException):
            with migrated.cursor() as cur:
                cur.execute(f"UPDATE validation_test SET {col} = 'rewritten' WHERE id = %s", (tid,))


# --------------------------------------------------------------------------
# Deterministic outcome: observed failure beats generated optimism.
# --------------------------------------------------------------------------
def test_observed_failure_outranks_interpretation(migrated):
    vid = ventures.create_venture(migrated, slug="v-optimism")
    tid = _test(migrated, vid, _hyp(migrated, vid), key="t",
                success_metric="conversion", success_comparator="GTE", success_threshold=0.10,
                kill_metric="conversion", kill_comparator="LT", kill_threshold=0.02)
    r = validation.record_result(migrated, validation_test_id=tid, result_key="r",
                                 observed_value={"conversion": 0.0}, interpretation="This still looks very promising!")
    assert r.outcome == "FAIL"  # derived from observed, not the optimistic interpretation
    row = validation.get_result(migrated, r.result_id)
    assert row[5] == "FAIL" and row[4] == "This still looks very promising!"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record")
        assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------
# Contradictory results coexist; no fabricated winner.
# --------------------------------------------------------------------------
def test_contradictory_results_coexist(migrated):
    vid = ventures.create_venture(migrated, slug="v-contra")
    tid = _test(migrated, vid, _hyp(migrated, vid), key="t",
                success_metric="conversion", success_comparator="GTE", success_threshold=0.10,
                kill_metric="conversion", kill_comparator="LT", kill_threshold=0.02)
    p = validation.record_result(migrated, validation_test_id=tid, result_key="rp", observed_value={"conversion": 0.15})
    f = validation.record_result(migrated, validation_test_id=tid, result_key="rf", observed_value={"conversion": 0.0})
    assert p.outcome == "PASS" and f.outcome == "FAIL"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM validation_result WHERE validation_test_id = %s", (tid,))
        assert cur.fetchone()[0] == 2  # both preserved
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'validation_test'")
        cols = {r[0] for r in cur.fetchall()}
        assert "final_result" not in cols  # no mutable winner


# --------------------------------------------------------------------------
# Provenance traversal.
# --------------------------------------------------------------------------
def test_provenance_traversal(migrated):
    vid = ventures.create_venture(migrated, slug="v-prov")
    opp, asm = _opp(migrated, vid), _assume(migrated, vid)
    hid = validation.create_hypothesis(migrated, vid, opportunity_id=opp, statement="s", hypothesis_key="h", assumption_id=asm).hypothesis_id
    tid = _test(migrated, vid, hid)
    r = validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"x": 1})
    prov = validation.provenance(migrated, r.result_id)
    assert prov["test"]["test_id"] == tid
    assert prov["hypothesis"]["opportunity_id"] == opp and prov["hypothesis"]["assumption_id"] == asm


def test_validation_has_no_governance_authority(migrated):
    vid = ventures.create_venture(migrated, slug="v-auth")
    tid = _test(migrated, vid, _hyp(migrated, vid))
    validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"x": 1})
    with migrated.cursor() as cur:
        for table in ("policy_decision", "kill_switch", "proof_receipt", "investment_decision_record", "capital_entry"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table
        cur.execute("SELECT count(*) FROM budget_account WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 0
