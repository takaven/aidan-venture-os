"""Gate 8 / Slice 3 — deterministic no-response completion + autonomy classification.

The response window is DERIVED (VERIFIED MARKET_ACTION proof time + precommitted
validation_test.max_duration_days); NO_RESPONSE is a deterministic derived fact, never a
market_observation. A future allocator can consume a persisted NO_RESPONSE completion with exact
provenance, but never as an automatic KILL. Autonomy classification is kernel-derived from
canonical evidence: predefined approvals keep a run CLEAN; any unplanned human intervention makes
it HUMAN_ASSISTED; and no synthetic fixture can be reported as a REAL clean-autonomous Alpha.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from aidan_core import approvals, commitment, execution, nextaction
from aidan_core.alpha import autonomy
from aidan_core.errors import MarketAuthorityError
from aidan_core.market import window as window_mod
from aidan_core.market.observation import record_market_observation

from factory_fakes import registry_with
from market_fakes import ChannelWorker, MarketSetup, freeze_outreach, market_action, operating_setup
from aidan_core.market import runtime as market_runtime


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _windowed(conn, slug, *, days=7, verify=True):
    """OPERATING venture + a validation_test WITH max_duration_days + a verified local market action."""
    s = operating_setup(conn, slug)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, statement, "
                    "statement_hash) VALUES (%s,%s,%s,%s,'h') RETURNING id",
                    (s.venture_id, f"wh-{slug}", s.opportunity_id, "reachable via outreach"))
        vh = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, method, "
            "success_criterion, evidence_required, max_spend, max_duration_days, success_metric, definition_hash) "
            "VALUES (%s,%s,%s,'OUTREACH','cold email','>=5 replies','logs','100',%s,'replies','h') RETURNING id",
            (s.venture_id, f"wt-{slug}", vh, days))
        vt = cur.fetchone()[0]
    setup = MarketSetup(s.venture_id, s.opportunity_id, vt)
    a = market_action(conn, s.venture_id, key=slug)
    spec = freeze_outreach(conn, setup, a)
    if verify:
        w = ChannelWorker(mode="compliant")
        market_runtime.execute_market_action(conn, a, registry=registry_with(w), worker_kind=w.kind)
        assert market_runtime.verify_market_action(conn, a, actual_cost=0).verified is True
    return setup, a, spec


def _window_start(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM proof_receipt WHERE action_request_id = %s "
                    "AND verification_type = 'MARKET_ACTION' AND result = 'VERIFIED'", (action_id,))
        return cur.fetchone()[0]


def _obs(conn, spec_id, eid, otype, *, occurred_at=None):
    return record_market_observation(conn, spec_id, external_event_id=eid, observation_type=otype,
                                     channel_kind="fake-local", occurred_at=occurred_at)


def _counts(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id = %s", (vid,))
        actions = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (vid,))
        decisions = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM capital_entry WHERE venture_id = %s", (vid,))
        capital = cur.fetchone()[0]
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        life = cur.fetchone()[0]
    return dict(actions=actions, decisions=decisions, capital=capital, life=life)


# ==========================================================================
# Window derivation (matrix 1-14)
# ==========================================================================
def test_1_13_window_derived_from_proof_and_duration(migrated):
    setup, a, spec = _windowed(migrated, "w1", days=7)
    start = _window_start(migrated, a)
    st = window_mod.market_window_status(migrated, spec.market_action_spec_id, as_of=start)
    assert st.status == window_mod.PENDING
    assert st.window_start_at == start
    assert st.window_end_at - st.window_start_at == timedelta(days=7)  # canonical duration


def test_2_no_verified_proof_unavailable(migrated):
    setup, a, spec = _windowed(migrated, "w2", verify=False)   # frozen but not executed/verified
    st = window_mod.market_window_status(migrated, spec.market_action_spec_id,
                                         as_of=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert st.status == window_mod.UNAVAILABLE


def test_5_pending_before_deadline(migrated):
    setup, a, spec = _windowed(migrated, "w5", days=7)
    start = _window_start(migrated, a)
    st = window_mod.market_window_status(migrated, spec.market_action_spec_id,
                                         as_of=start + timedelta(days=7) - timedelta(seconds=1))
    assert st.status == window_mod.PENDING


def test_6_responded_reply_within_window(migrated):
    setup, a, spec = _windowed(migrated, "w6", days=7)
    start = _window_start(migrated, a)
    _obs(migrated, spec.market_action_spec_id, "r1", "REPLIED", occurred_at=start + timedelta(days=1))
    st = window_mod.market_window_status(migrated, spec.market_action_spec_id,
                                         as_of=start + timedelta(days=30))
    assert st.status == window_mod.RESPONDED


def test_7_no_response_at_and_after_deadline(migrated):
    setup, a, spec = _windowed(migrated, "w7", days=7)
    start = _window_start(migrated, a)
    at = window_mod.market_window_status(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=7))
    after = window_mod.market_window_status(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=8))
    assert at.status == window_mod.NO_RESPONSE and after.status == window_mod.NO_RESPONSE  # equality = elapsed


def test_8_9_10_delivered_bounced_unsub_not_a_response(migrated):
    setup, a, spec = _windowed(migrated, "w8", days=7)
    start = _window_start(migrated, a)
    _obs(migrated, spec.market_action_spec_id, "d1", "DELIVERED", occurred_at=start + timedelta(days=1))
    _obs(migrated, spec.market_action_spec_id, "b1", "BOUNCED", occurred_at=start + timedelta(days=1))
    _obs(migrated, spec.market_action_spec_id, "u1", "UNSUBSCRIBE", occurred_at=start + timedelta(days=1))
    st = window_mod.market_window_status(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=8))
    assert st.status == window_mod.NO_RESPONSE   # none of these is a qualifying REPLIED
    # negative evidence retained unchanged
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM market_observation WHERE market_action_spec_id = %s AND observation_type IN "
                    "('BOUNCED','UNSUBSCRIBE')", (spec.market_action_spec_id,))
        assert cur.fetchone()[0] == 2


def test_7_completion_persist_idempotent_and_gated(migrated):
    setup, a, spec = _windowed(migrated, "wc", days=7)
    start = _window_start(migrated, a)
    # 12: cannot force before deadline
    with pytest.raises(MarketAuthorityError):
        window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=1))
    c1 = window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=7))
    c2 = window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=9))
    assert c1.created is True and c2.created is False and c1.completion_hash == c2.completion_hash


def test_completion_refused_when_response_exists(migrated):
    setup, a, spec = _windowed(migrated, "wr", days=7)
    start = _window_start(migrated, a)
    _obs(migrated, spec.market_action_spec_id, "r1", "REPLIED", occurred_at=start + timedelta(days=1))
    with pytest.raises(MarketAuthorityError):
        window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=8))


def test_11_no_response_not_an_observation_type(migrated):
    from aidan_core.market import observation as obs_mod
    assert "NO_RESPONSE" not in obs_mod.OBSERVATION_TYPES
    setup, a, spec = _windowed(migrated, "w11", days=7)
    with pytest.raises(MarketAuthorityError):
        record_market_observation(migrated, spec.market_action_spec_id, external_event_id="e",
                                  observation_type="NO_RESPONSE", channel_kind="fake-local")


def test_14_no_duplicate_deadline_field_on_spec(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'market_action_spec' "
                    "AND column_name IN ('response_window_ends_at','observation_deadline','window_ends_at','deadline')")
        assert cur.fetchone()[0] == 0


# ==========================================================================
# Late evidence (matrix 15-19)
# ==========================================================================
def test_15_16_17_late_reply_after_no_response(migrated):
    setup, a, spec = _windowed(migrated, "w15", days=7)
    start = _window_start(migrated, a)
    c = window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=7))
    # a late reply AFTER the deadline is still recorded as evidence ...
    late = _obs(migrated, spec.market_action_spec_id, "late", "REPLIED", occurred_at=start + timedelta(days=20))
    assert late.created is True
    # ... and the prior completion fact remains reconstructable + unchanged
    row = window_mod.no_response_completion_for(migrated, spec.market_action_spec_id)
    assert str(row[0]) == c.market_window_completion_id
    # the deadline-time truth is preserved: status as-of the deadline is still NO_RESPONSE
    at = window_mod.market_window_status(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=7))
    assert at.status == window_mod.NO_RESPONSE


# ==========================================================================
# Allocator provenance (matrix 20-24)
# ==========================================================================
def test_20_24_no_response_recommendation_cites_completion_not_kill(migrated):
    setup, a, spec = _windowed(migrated, "w20", days=7)
    start = _window_start(migrated, a)
    c = window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=7))
    rec = nextaction.recommend(migrated, setup.venture_id, setup.opportunity_id, recommendation_key="k1")
    # NO_RESPONSE drives another bounded market test (not an automatic KILL); the completion is cited
    assert rec.action_type == "MARKET" and rec.reason_code != "KILL_CRITERION_TRIGGERED"
    prov = nextaction.provenance(migrated, rec.recommendation_id)
    assert [str(x) for x in prov["considered_completions"]] == [c.market_window_completion_id]


def test_21_cross_venture_completion_isolation(migrated):
    a1 = _windowed(migrated, "w21a", days=7)
    a2 = _windowed(migrated, "w21b", days=7)
    s1, act1, spec1 = a1
    start1 = _window_start(migrated, act1)
    window_mod.record_no_response_completion(migrated, spec1.market_action_spec_id, as_of=start1 + timedelta(days=7))
    # venture-2's recommendation does not cite venture-1's completion
    rec2 = nextaction.recommend(migrated, a2[0].venture_id, a2[0].opportunity_id, recommendation_key="k1")
    assert nextaction.provenance(migrated, rec2.recommendation_id)["considered_completions"] == []


def test_22_23_completion_replay_and_new_evidence(migrated):
    setup, a, spec = _windowed(migrated, "w22", days=7)
    start = _window_start(migrated, a)
    window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=7))
    r1 = nextaction.recommend(migrated, setup.venture_id, setup.opportunity_id, recommendation_key="k1")
    r1b = nextaction.recommend(migrated, setup.venture_id, setup.opportunity_id, recommendation_key="k1")
    assert r1b.created is False and r1b.recommendation_id == r1.recommendation_id   # replay converges
    # a later reply changes the input identity for a NEW key
    _obs(migrated, spec.market_action_spec_id, "late", "REPLIED", occurred_at=start + timedelta(days=20))
    r2 = nextaction.recommend(migrated, setup.venture_id, setup.opportunity_id, recommendation_key="k2")
    prov2 = nextaction.provenance(migrated, r2.recommendation_id)
    assert len(prov2["considered_observations"]) == 1  # now also cites the late reply


# ==========================================================================
# Autonomy classification (matrix 25-35)
# ==========================================================================
def test_25_35_predefined_approval_stays_clean(migrated):
    s = operating_setup(migrated, "au25")
    # a real predefined approval via the Gate-1 path does not make the run human-assisted
    aid = market_action(migrated, s.venture_id, key="au25", required_autonomy=2)
    pre = execution.request_execution(migrated, aid)
    approvals.approve(migrated, pre.approval_id, decided_by="board")
    assert autonomy.assistance_class(migrated, s.venture_id) == autonomy.CLEAN
    # predefined approval cannot be recorded as an intervention
    with pytest.raises(ValueError):
        autonomy.record_intervention(migrated, s.venture_id, intervention_kind="PREDEFINED_APPROVAL",
                                     intervention_stage="approval", occurred_at=datetime(2030, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("kind", ["REASONING_CORRECTION", "CODE_REPAIR", "DEPLOYMENT_REPAIR",
                                  "PROVIDER_REPAIR", "OUTCOME_TRANSCRIPTION"])
def test_26_to_30_unplanned_intervention_is_human_assisted(migrated, kind):
    s = operating_setup(migrated, f"au{kind[:4]}")
    assert autonomy.assistance_class(migrated, s.venture_id) == autonomy.CLEAN
    autonomy.record_intervention(migrated, s.venture_id, intervention_kind=kind,
                                 intervention_stage="run", occurred_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert autonomy.assistance_class(migrated, s.venture_id) == autonomy.HUMAN_ASSISTED


def test_31_multiple_interventions_remain_human_assisted(migrated):
    s = operating_setup(migrated, "au31")
    for k in ("REASONING_CORRECTION", "CODE_REPAIR"):
        autonomy.record_intervention(migrated, s.venture_id, intervention_kind=k, intervention_stage="run",
                                     occurred_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert autonomy.assistance_class(migrated, s.venture_id) == autonomy.HUMAN_ASSISTED


def test_32_other_venture_intervention_ignored(migrated):
    a = operating_setup(migrated, "au32a")
    b = operating_setup(migrated, "au32b")
    autonomy.record_intervention(migrated, b.venture_id, intervention_kind="CODE_REPAIR",
                                 intervention_stage="run", occurred_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert autonomy.assistance_class(migrated, a.venture_id) == autonomy.CLEAN       # A unaffected
    assert autonomy.assistance_class(migrated, b.venture_id) == autonomy.HUMAN_ASSISTED


def test_33_intervention_immutable(migrated):
    s = operating_setup(migrated, "au33")
    iid = autonomy.record_intervention(migrated, s.venture_id, intervention_kind="REASONING_CORRECTION",
                                       intervention_stage="run", occurred_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    with migrated.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE alpha_intervention SET intervention_kind = 'CODE_REPAIR' WHERE id = %s", (iid,))
    with migrated.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("DELETE FROM alpha_intervention WHERE id = %s", (iid,))


# ==========================================================================
# Real / simulated boundary (matrix 42-44)
# ==========================================================================
def test_42_43_44_synthetic_run_never_real_clean_alpha(migrated):
    s = operating_setup(migrated, "au42")
    summary = autonomy.autonomy_summary(migrated, s.venture_id)
    # clean assistance, but evidence is SIMULATED -> not a real clean-autonomous Alpha
    assert summary["assistance_class"] == autonomy.CLEAN
    assert summary["evidence_class"] == autonomy.SIMULATED
    assert summary["clean_autonomous_alpha"] is False
    assert autonomy.is_clean_autonomous_alpha(migrated, s.venture_id) is False


# ==========================================================================
# Authority boundary (matrix 36-41)
# ==========================================================================
def test_36_to_41_window_and_autonomy_have_no_authority(migrated):
    setup, a, spec = _windowed(migrated, "w36", days=7)
    vid = setup.venture_id
    start = _window_start(migrated, a)
    before = _counts(migrated, vid)
    window_mod.market_window_status(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=8))
    window_mod.record_no_response_completion(migrated, spec.market_action_spec_id, as_of=start + timedelta(days=8))
    autonomy.record_intervention(migrated, vid, intervention_kind="REASONING_CORRECTION",
                                 intervention_stage="run", occurred_at=start)
    autonomy.assistance_class(migrated, vid)
    after = _counts(migrated, vid)
    # truth projection only: no ActionRequest / decision / capital / lifecycle change
    assert after == before


# ==========================================================================
# Regression (matrix 45-50)
# ==========================================================================
def test_48_validation_only_recommendation_unchanged(migrated):
    s = operating_setup(migrated, "w48")   # no market evidence / no completion
    rec = nextaction.recommend(migrated, s.venture_id, s.opportunity_id, recommendation_key="k1")
    assert rec.action_type == "HOLD" and rec.reason_code == "NO_HIGH_VALUE_ACTION_NOW"


def test_49_slice3_added_its_canonical_entities(migrated):
    # forward-stable Slice-3 invariant: Slice 3 introduced exactly its three append-only entities
    # (not a global migration ceiling — later slices add migrations legitimately).
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.market_window_completion'), "
                    "to_regclass('public.recommendation_market_window_completion'), "
                    "to_regclass('public.alpha_intervention')")
        assert all(x is not None for x in cur.fetchone())
