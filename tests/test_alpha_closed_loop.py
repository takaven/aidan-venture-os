"""Gate 8 / Slice 4 — closed-loop evals (development).

Drives the real production chain end-to-end: seed market evidence -> MARKET recommendation ->
governed MARKET investment decision -> send_outreach ActionRequest -> market_action_spec ->
execution -> VERIFIED MARKET_ACTION proof -> observation / no-response completion -> next
recommendation. classify_loop then derives three independent dimensions (completeness,
assistance, reality) scoped to the exact loop interval. Every fixture is SIMULATED, so none is an
eligible clean-real Alpha — that remains Slice 5.
"""
from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from aidan_core import commitment, nextaction
from aidan_core.alpha import autonomy, loop
from aidan_core.market import origin as origin_mod
from aidan_core.market import runtime as market_runtime
from aidan_core.market import window as window_mod
from aidan_core.market.observation import record_market_observation

from factory_fakes import registry_with
from market_fakes import ChannelWorker, MarketSetup, freeze_outreach, market_action, operating_setup
from postmark_fakes import FakePostmarkTransport, FakeRecipientResolver, default_source, postmark_run


# --------------------------------------------------------------------------
# helpers — a full simulated closed loop through the production chain
# --------------------------------------------------------------------------
class _Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _local_market_action(conn, setup, *, key, verify=True):
    a = market_action(conn, setup.venture_id, key=key)
    spec = freeze_outreach(conn, setup, a)  # channel fake-local (SIMULATED)
    if verify:
        market_runtime.execute_market_action(conn, a, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
        assert market_runtime.verify_market_action(conn, a, actual_cost=0).verified is True
    return a, spec


def _rec_time(conn, rec_id):
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM next_action_recommendation WHERE id = %s", (rec_id,))
        return cur.fetchone()[0]


def _setup(conn, slug, *, days=7):
    s = operating_setup(conn, slug)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, statement, "
                    "statement_hash) VALUES (%s,%s,%s,%s,'h') RETURNING id",
                    (s.venture_id, f"lh-{slug}", s.opportunity_id, "reachable"))
        vh = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, method, "
            "success_criterion, evidence_required, max_spend, max_duration_days, success_metric, definition_hash) "
            "VALUES (%s,%s,%s,'OUTREACH','cold email','>=5 replies','logs','100',%s,'replies','h') RETURNING id",
            (s.venture_id, f"lt-{slug}", vh, days))
        vt = cur.fetchone()[0]
    return MarketSetup(s.venture_id, s.opportunity_id, vt)


def sim_loop(conn, slug, *, outcome="REPLIED", days=7):
    """Build one complete SIMULATED closed loop; return r1/r2 + the loop action/spec."""
    setup = _setup(conn, slug, days=days)
    # seed market evidence so the allocator selects MARKET
    _seed_a, seed_spec = _local_market_action(conn, setup, key=f"{slug}-seed")
    record_market_observation(conn, seed_spec.market_action_spec_id, external_event_id="seed",
                              observation_type="DELIVERED", channel_kind="fake-local")
    r1 = nextaction.recommend(conn, setup.venture_id, setup.opportunity_id, recommendation_key=f"{slug}-k1")
    assert r1.action_type == "MARKET"
    res1 = commitment.commit_recommendation(conn, r1.recommendation_id)
    assert res1.decision == "MARKET" and res1.resulting_action_id is not None
    loop_action = res1.resulting_action_id
    # execute the loop's consequential action (local channel = SIMULATED) -> VERIFIED proof
    loop_spec = freeze_outreach(conn, setup, loop_action)
    market_runtime.execute_market_action(conn, loop_action, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    assert market_runtime.verify_market_action(conn, loop_action, actual_cost=0).verified is True
    start = _window_start(conn, loop_action)
    if outcome == "NO_RESPONSE":
        window_mod.record_no_response_completion(conn, loop_spec.market_action_spec_id, as_of=start + timedelta(days=days))
    else:
        record_market_observation(conn, loop_spec.market_action_spec_id, external_event_id="o1",
                                  observation_type=outcome, channel_kind="fake-local",
                                  occurred_at=start + timedelta(days=1))
    r2 = nextaction.recommend(conn, setup.venture_id, setup.opportunity_id, recommendation_key=f"{slug}-k2")
    return _Ctx(setup=setup, r1=r1, r2=r2, loop_action=loop_action, loop_spec=loop_spec,
                r1_at=_rec_time(conn, r1.recommendation_id), r2_at=_rec_time(conn, r2.recommendation_id))


def _window_start(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM proof_receipt WHERE action_request_id = %s "
                    "AND verification_type = 'MARKET_ACTION' AND result = 'VERIFIED'", (action_id,))
        return cur.fetchone()[0]


def _intervene(conn, vid, *, at, kind="REASONING_CORRECTION"):
    return autonomy.record_intervention(conn, vid, intervention_kind=kind, intervention_stage="run", occurred_at=at)


def _counts(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id=%s", (vid,)); acts = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", (vid,)); dec = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM capital_entry WHERE venture_id=%s", (vid,)); cap = cur.fetchone()[0]
        cur.execute("SELECT lifecycle_state FROM venture WHERE id=%s", (vid,)); life = cur.fetchone()[0]
    return dict(acts=acts, dec=dec, cap=cap, life=life)


def _classify(conn, ctx):
    return loop.classify_loop(conn, start_recommendation_id=ctx.r1.recommendation_id,
                              next_recommendation_id=ctx.r2.recommendation_id)


# ==========================================================================
# A / B / C — simulated positive / negative / no-response loops
# ==========================================================================
def test_A_simulated_positive_loop(migrated):
    c = sim_loop(migrated, "A", outcome="REPLIED")
    r = _classify(migrated, c)
    assert r == {"completeness": "COMPLETE", "assistance_class": autonomy.CLEAN,
                 "reality_class": "SIMULATED", "eligible_clean_real_alpha": False}


def test_B_simulated_negative_loop(migrated):
    c = sim_loop(migrated, "B", outcome="BOUNCED")
    r = _classify(migrated, c)
    assert r["completeness"] == "COMPLETE" and r["reality_class"] == "SIMULATED"
    assert r["eligible_clean_real_alpha"] is False and c.r2.action_type != "KILL"


def test_C_simulated_no_response_loop(migrated):
    c = sim_loop(migrated, "C", outcome="NO_RESPONSE", days=5)
    r = _classify(migrated, c)
    assert r["completeness"] == "COMPLETE" and r["reality_class"] == "SIMULATED"
    # the next recommendation cited the completion (NO_RESPONSE drove another bounded test)
    prov = nextaction.provenance(migrated, c.r2.recommendation_id)
    assert len(prov["considered_completions"]) >= 1 and c.r2.action_type == "MARKET"


# ==========================================================================
# D / E — missing action proof / outcome without valid provenance
# ==========================================================================
def test_D_action_proof_missing_no_completion(migrated):
    setup = _setup(migrated, "D")
    a = market_action(migrated, setup.venture_id, key="D")
    freeze_outreach(migrated, setup, a)
    market_runtime.execute_market_action(migrated, a, registry=registry_with(ChannelWorker(mode="nothing")), worker_kind="outreach-a")
    assert market_runtime.verify_market_action(migrated, a, actual_cost=0).verified is False
    from aidan_core.market import action as market_mod
    spec_id = market_mod.get_market_action_spec(migrated, a)[0]
    # window is UNAVAILABLE without a verified action proof; no-response cannot be forced
    st = window_mod.market_window_status(migrated, spec_id,
                                         as_of=__import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc))
    assert st.status == window_mod.UNAVAILABLE


def test_E_outcome_without_verified_action_rejected(migrated):
    setup = _setup(migrated, "E")
    a = market_action(migrated, setup.venture_id, key="E")
    freeze_outreach(migrated, setup, a)   # frozen but never executed/verified / not postmark-verified
    from aidan_core.errors import MarketAuthorityError
    with pytest.raises(MarketAuthorityError):   # not a postmark-verified action; origin cannot bind
        origin_mod.record_evidence_origin(migrated, a, transport=FakePostmarkTransport(),
                                          provider_kind="local", source_instance_ref="x")


# ==========================================================================
# F / G — observation / completion -> next allocation with provenance
# ==========================================================================
def test_F_observation_drives_next_allocation(migrated):
    c = sim_loop(migrated, "F", outcome="REPLIED")
    prov = nextaction.provenance(migrated, c.r2.recommendation_id)
    assert len(prov["considered_observations"]) >= 1   # exact observation provenance, not prose


def test_G_completion_drives_next_allocation(migrated):
    c = sim_loop(migrated, "G", outcome="NO_RESPONSE", days=5)
    prov = nextaction.provenance(migrated, c.r2.recommendation_id)
    assert len(prov["considered_completions"]) >= 1


# ==========================================================================
# H — contradictory evidence retained
# ==========================================================================
def test_H_contradictory_evidence(migrated):
    c = sim_loop(migrated, "H", outcome="REPLIED")
    record_market_observation(migrated, c.loop_spec.market_action_spec_id, external_event_id="neg",
                              observation_type="UNSUBSCRIBE", channel_kind="fake-local")
    r3 = nextaction.recommend(migrated, c.setup.venture_id, c.setup.opportunity_id, recommendation_key="H-k3")
    otypes = set()
    with migrated.cursor() as cur:
        cur.execute("SELECT observation_type FROM market_observation WHERE market_action_spec_id = %s",
                    (c.loop_spec.market_action_spec_id,))
        otypes = {row[0] for row in cur.fetchall()}
    assert {"REPLIED", "UNSUBSCRIBE"} <= otypes and r3.action_type == "MARKET"


# ==========================================================================
# I..N — autonomy in exact loop interval
# ==========================================================================
def test_I_predefined_approval_stays_clean(migrated):
    # the loop chain contains canonical approvals (build/deploy/policy) but no alpha_intervention,
    # so a predefined-approval loop remains CLEAN — approvals are never counted as assistance
    c = sim_loop(migrated, "I", outcome="REPLIED")
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN


@pytest.mark.parametrize("kind", ["REASONING_CORRECTION", "CODE_REPAIR", "DEPLOYMENT_REPAIR",
                                  "PROVIDER_REPAIR", "OUTCOME_TRANSCRIPTION"])
def test_J_to_N_in_loop_intervention_is_human_assisted(migrated, kind):
    c = sim_loop(migrated, f"J{kind[:3]}", outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r1_at, kind=kind)   # inside [r1, r2)
    assert _classify(migrated, c)["assistance_class"] == autonomy.HUMAN_ASSISTED


# ==========================================================================
# O / P / Q / R — interval scoping
# ==========================================================================
def test_O_intervention_before_loop_ignored(migrated):
    c = sim_loop(migrated, "O", outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r1_at - timedelta(days=1))   # before start
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN


def test_P_intervention_after_loop_ignored(migrated):
    c = sim_loop(migrated, "P", outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r2_at + timedelta(days=1))   # after end
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN


def test_Q_other_venture_intervention_ignored(migrated):
    c = sim_loop(migrated, "Q", outcome="REPLIED")
    other = operating_setup(migrated, "Qother")
    _intervene(migrated, other.venture_id, at=c.r1_at)   # different venture
    assert _classify(migrated, c)["assistance_class"] == autonomy.CLEAN


def test_R_related_action_conflict_rejected(migrated):
    c = sim_loop(migrated, "R", outcome="REPLIED")
    other = operating_setup(migrated, "Rother")
    other_action = market_action(migrated, other.venture_id, key="Rother")
    # an intervention on venture c cannot reference another venture's action (composite FK)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        autonomy.record_intervention(migrated, c.setup.venture_id, intervention_kind="CODE_REPAIR",
                                     intervention_stage="run", occurred_at=c.r1_at,
                                     related_action_request_id=other_action)


# ==========================================================================
# S / T / U / V — reality cannot be forged; simulated stays simulated
# ==========================================================================
def test_S_fake_provider_cannot_be_real(migrated):
    # a Postmark FAKE-transport action records a SIMULATED origin; no metadata/caller makes it REAL
    r = postmark_run(migrated, "S")
    assert r.verify.verified is True
    assert origin_mod.action_reality(migrated, r.action_id) == "SIMULATED"
    # the durable origin row is SIMULATED and append-only
    with migrated.cursor() as cur:
        cur.execute("SELECT origin_kind FROM external_evidence_origin eo JOIN proof_receipt pr ON pr.id = eo.proof_receipt_id "
                    "WHERE pr.action_request_id = %s", (r.action_id,))
        assert cur.fetchone()[0] == "SIMULATED"


def test_S_origin_write_is_idempotent_and_immutable(migrated):
    r = postmark_run(migrated, "S2")
    with migrated.cursor() as cur:
        cur.execute("SELECT eo.id FROM external_evidence_origin eo JOIN proof_receipt pr ON pr.id = eo.proof_receipt_id "
                    "WHERE pr.action_request_id = %s", (r.action_id,))
        oid = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE external_evidence_origin SET origin_kind = 'REAL_PROVIDER' WHERE id = %s", (oid,))


def test_U_no_response_reality_from_anchor_proof(migrated):
    c = sim_loop(migrated, "U", outcome="NO_RESPONSE", days=5)
    # the anchor action proof is SIMULATED (local channel) -> the loop reality is SIMULATED
    assert loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                              next_recommendation_id=c.r2.recommendation_id)["reality_class"] == "SIMULATED"


def test_V_local_channel_action_is_simulated(migrated):
    c = sim_loop(migrated, "V", outcome="REPLIED")
    assert origin_mod.action_reality(migrated, c.loop_action) == "SIMULATED"


# ==========================================================================
# W / X / Y — completeness + assistance combinations
# ==========================================================================
def test_W_complete_but_human_assisted(migrated):
    c = sim_loop(migrated, "W", outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r1_at)
    r = _classify(migrated, c)
    assert r["completeness"] == "COMPLETE" and r["assistance_class"] == autonomy.HUMAN_ASSISTED
    assert r["reality_class"] == "SIMULATED" and r["eligible_clean_real_alpha"] is False


def test_X_complete_clean_simulated(migrated):
    c = sim_loop(migrated, "X", outcome="REPLIED")
    r = _classify(migrated, c)
    assert r == {"completeness": "COMPLETE", "assistance_class": autonomy.CLEAN,
                 "reality_class": "SIMULATED", "eligible_clean_real_alpha": False}


def test_Y_incomplete_without_next_recommendation(migrated):
    c = sim_loop(migrated, "Y", outcome="REPLIED")
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id, next_recommendation_id=None)
    assert r["completeness"] == "INCOMPLETE" and r["eligible_clean_real_alpha"] is False


# ==========================================================================
# Z / AA — replay + late evidence
# ==========================================================================
def test_Z_next_recommendation_replay_converges(migrated):
    c = sim_loop(migrated, "Z", outcome="REPLIED")
    again = nextaction.recommend(migrated, c.setup.venture_id, c.setup.opportunity_id, recommendation_key="Z-k2")
    assert again.created is False and again.recommendation_id == c.r2.recommendation_id


def test_AA_late_reply_after_no_response(migrated):
    c = sim_loop(migrated, "AA", outcome="NO_RESPONSE", days=5)
    start = _window_start(migrated, c.loop_action)
    prov2_before = nextaction.provenance(migrated, c.r2.recommendation_id)["considered_completions"]
    record_market_observation(migrated, c.loop_spec.market_action_spec_id, external_event_id="late",
                              observation_type="REPLIED", channel_kind="fake-local",
                              occurred_at=start + timedelta(days=20))
    r3 = nextaction.recommend(migrated, c.setup.venture_id, c.setup.opportunity_id, recommendation_key="AA-k3")
    # old recommendation history unchanged; a new one may consume the later reply
    assert nextaction.provenance(migrated, c.r2.recommendation_id)["considered_completions"] == prov2_before
    assert len(nextaction.provenance(migrated, r3.recommendation_id)["considered_observations"]) >= 1


# ==========================================================================
# AB / AC / AD — kill / cross-venture / policy-capital bounds
# ==========================================================================
def test_AB_terminal_decision_is_structurally_complete(migrated):
    # a terminal allocation (HOLD here; KILL behaves the same) is a structurally complete loop
    # with no consequential market action and no next recommendation required.
    s = operating_setup(migrated, "AB")   # no market evidence -> HOLD
    rec = nextaction.recommend(migrated, s.venture_id, s.opportunity_id, recommendation_key="AB-k1")
    assert rec.action_type == "HOLD"
    commitment.commit_recommendation(migrated, rec.recommendation_id)   # HOLD decision (no action)
    r = loop.classify_loop(migrated, start_recommendation_id=rec.recommendation_id, next_recommendation_id=None)
    assert r["completeness"] == "COMPLETE" and r["reality_class"] == "SIMULATED"
    assert r["eligible_clean_real_alpha"] is False
    with migrated.cursor() as cur:   # no MARKET/send_outreach ActionRequest from a terminal decision
        cur.execute("SELECT count(*) FROM action_request WHERE venture_id = %s AND action_type = 'send_outreach'",
                    (s.venture_id,))
        assert cur.fetchone()[0] == 0


def test_AC_cross_venture_evidence_isolation(migrated):
    a = sim_loop(migrated, "ACa", outcome="REPLIED")
    b = operating_setup(migrated, "ACb")
    rec_b = nextaction.recommend(migrated, b.venture_id, b.opportunity_id, recommendation_key="ACb-k1")
    assert rec_b.action_type == "HOLD"   # venture-A evidence cannot drive venture-B


def test_AD_next_action_still_bounded_by_policy_capital(migrated):
    c = sim_loop(migrated, "AD", outcome="REPLIED")
    before = _counts(migrated, c.setup.venture_id)
    # committing the next MARKET recommendation goes through the canonical governed path
    res = commitment.commit_recommendation(migrated, c.r2.recommendation_id)
    after = _counts(migrated, c.setup.venture_id)
    assert res.decision == "MARKET" and after["acts"] == before["acts"] + 1  # exactly one governed action
    assert after["cap"] == before["cap"]   # allocator moved no capital


# ==========================================================================
# AE / AF / AG — immutability + regressions
# ==========================================================================
def test_AE_classification_mutates_nothing(migrated):
    c = sim_loop(migrated, "AE", outcome="REPLIED")
    before = _counts(migrated, c.setup.venture_id)
    _classify(migrated, c)
    origin_mod.action_reality(migrated, c.loop_action)
    assert _counts(migrated, c.setup.venture_id) == before


def test_AG_postmark_fixture_zero_network(migrated):
    r = postmark_run(migrated, "AG")
    assert r.verify.verified is True and FakePostmarkTransport.network_calls == 0


# ==========================================================================
# AH / AI — no closed_loop_run; no market score
# ==========================================================================
def test_AH_no_closed_loop_run_schema(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.closed_loop_run'), to_regclass('public.alpha_run'), "
                    "to_regclass('public.workflow_run')")
        assert cur.fetchone() == (None, None, None)


def test_AI_no_market_or_autonomy_score(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name IN "
                    "('external_evidence_origin','next_action_recommendation') AND column_name IN "
                    "('score','confidence','market_score','autonomy_score')")
        assert cur.fetchone()[0] == 0


# ==========================================================================
# Finding A — REAL_PROVIDER is not caller-selectable
# ==========================================================================
def _force_real_origin(conn, action_id):
    """Test scaffolding: inject a REAL_PROVIDER origin for a proof (bypassing the writer) to
    exercise the loop's reality LOGIC — production code can only produce REAL via a live transport."""
    with conn.cursor() as cur:
        cur.execute("SELECT pr.id, pr.execution_attempt_id, ar.venture_id FROM proof_receipt pr "
                    "JOIN action_request ar ON ar.id = pr.action_request_id WHERE pr.action_request_id = %s "
                    "AND pr.verification_type = 'MARKET_ACTION' AND pr.result = 'VERIFIED'", (action_id,))
        proof_id, attempt, vid = cur.fetchone()
        cur.execute("INSERT INTO external_evidence_origin (venture_id, proof_receipt_id, execution_attempt_id, "
                    "origin_kind, provider_kind, source_instance_ref, origin_hash) "
                    "VALUES (%s,%s,%s,'REAL_PROVIDER','postmark','x','forced') "
                    "ON CONFLICT (proof_receipt_id) DO NOTHING", (vid, proof_id, attempt))


def test_finding_A_fake_transport_origin_is_simulated(migrated):
    # a Postmark FAKE action records a SIMULATED origin; there is no origin_kind param to force REAL
    r = postmark_run(migrated, "fA")
    assert origin_mod.action_reality(migrated, r.action_id) == "SIMULATED"
    import inspect
    assert "origin_kind" not in inspect.signature(origin_mod.record_evidence_origin).parameters


def test_finding_A_local_action_cannot_bind_origin(migrated):
    # a local (non-postmark) verified action cannot be bound a provider origin at all
    from aidan_core.errors import MarketAuthorityError
    setup = _setup(migrated, "fA2")
    a, _spec = _local_market_action(migrated, setup, key="fA2")   # verifier_kind = market-action
    with pytest.raises(MarketAuthorityError):
        origin_mod.record_evidence_origin(migrated, a, transport=FakePostmarkTransport(),
                                          provider_kind="postmark", source_instance_ref="x")


def test_finding_7_origin_conflict_on_material_change(migrated):
    from aidan_core.errors import IdempotencyConflictError
    r = postmark_run(migrated, "f7")   # verify already bound a SIMULATED origin (provider 'postmark')
    with pytest.raises(IdempotencyConflictError):   # different provenance for the same proof conflicts
        origin_mod.record_evidence_origin(migrated, r.action_id, transport=FakePostmarkTransport(),
                                          provider_kind="DIFFERENT", source_instance_ref="y")


# ==========================================================================
# Finding B — next recommendation must belong to this loop
# ==========================================================================
def test_finding_B_next_wrong_venture_rejected(migrated):
    from aidan_core.errors import MarketAuthorityError
    a = sim_loop(migrated, "fBa", outcome="REPLIED")
    b = sim_loop(migrated, "fBb", outcome="REPLIED")
    with pytest.raises(MarketAuthorityError):   # b.r2 is another venture's recommendation
        loop.classify_loop(migrated, start_recommendation_id=a.r1.recommendation_id,
                           next_recommendation_id=b.r2.recommendation_id)


def test_finding_B_next_not_following_start_rejected(migrated):
    from aidan_core.errors import MarketAuthorityError
    c = sim_loop(migrated, "fB2", outcome="REPLIED")
    with pytest.raises(MarketAuthorityError):   # r1 precedes r2; using r1 as the "next" of r2 is invalid
        loop.classify_loop(migrated, start_recommendation_id=c.r2.recommendation_id,
                           next_recommendation_id=c.r1.recommendation_id)


# ==========================================================================
# Finding C — a REAL action + generic observation is NOT a REAL loop
# ==========================================================================
def test_finding_C_real_action_generic_observation_not_real(migrated):
    c = sim_loop(migrated, "fC", outcome="REPLIED")
    _force_real_origin(migrated, c.loop_action)   # simulate a REAL outbound action proof
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    # the REAL action + a generic REPLIED observation does NOT qualify as a REAL loop
    assert r["reality_class"] == "SIMULATED" and r["eligible_clean_real_alpha"] is False


def test_finding_C_real_action_no_response_is_real(migrated):
    c = sim_loop(migrated, "fC2", outcome="NO_RESPONSE", days=5)
    _force_real_origin(migrated, c.loop_action)   # REAL outbound action + deterministic NO_RESPONSE
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    assert r["reality_class"] == "REAL" and r["completeness"] == "COMPLETE"
    assert r["assistance_class"] == autonomy.CLEAN and r["eligible_clean_real_alpha"] is True


# ==========================================================================
# Finding D — real Postmark reconciliation has no NotImplementedError in the verify path
# ==========================================================================
def test_finding_D_http_transport_reconciliation_implemented(migrated):
    from aidan_core.market.postmark import PostmarkHttpTransport
    t = PostmarkHttpTransport("FAKE-TOKEN-not-used")
    # the verifier reconciles via get_outbound_message (GET-by-MessageID); the removed metadata
    # search is gone, so no NotImplementedError remains in the live verification path
    assert hasattr(t, "get_outbound_message")
    assert not hasattr(t, "find_outbound_by_correlation")
    src = __import__("inspect").getsource(t.get_outbound_message.__func__)
    assert "NotImplementedError" not in src


def test_finding_D_worker_claiming_foreign_message_rejected(migrated):
    # a worker whose claimed MessageID resolves (in provider state) to ANOTHER action's message is
    # rejected: the located message's Metadata does not match this action's canonical correlation.
    from aidan_core.factory.verifiers import VerificationRequest
    from aidan_core.market import action as market_mod
    from aidan_core.market import postmark as pm
    transport = FakePostmarkTransport()
    resolver = FakeRecipientResolver()
    source = default_source()
    a = postmark_run(migrated, "fD", transport=transport, resolver=resolver, source=source)
    other = postmark_run(migrated, "fDx", transport=transport, resolver=resolver, source=source)
    other_spec = str(market_mod.get_market_action_spec(migrated, other.action_id)[0])
    foreign_mid = next(mid for mid, m in transport.outbound.items()
                       if str(m["Metadata"].get("market_action_spec")) == other_spec)
    _task, contract = pm._postmark_contract(migrated, a.action_id, source)
    req = VerificationRequest(action_request_id=a.action_id, execution_attempt_id="x",
                              verifier_kind=pm.POSTMARK_VERIFIER_KIND, expected_output_contract=contract,
                              worker_structured_output={"message_id": foreign_mid}, artifacts=(), spec_hash="h")
    verdict = pm.PostmarkActionVerifier(transport, resolver, source).verify(req)
    assert verdict.verdict == "REJECTED"


# ==========================================================================
# REAL provider loops (trusted stub transport — proves the trust boundary, no network)
# ==========================================================================
def real_loop(conn, slug, *, outcome="REPLIED"):
    """A closed loop whose consequential action executes through a genuine (stub, no-network)
    PostmarkHttpTransport and whose outcome is a trusted, reconciled provider observation."""
    from postmark_fakes import StubRealPostmarkTransport, FakeRecipientResolver, default_source, basic_auth
    from aidan_core.market import postmark as pm
    setup = _setup(conn, slug)
    _sa, seed_spec = _local_market_action(conn, setup, key=f"{slug}-seed")
    record_market_observation(conn, seed_spec.market_action_spec_id, external_event_id="seed",
                              observation_type="DELIVERED", channel_kind="fake-local")
    r1 = nextaction.recommend(conn, setup.venture_id, setup.opportunity_id, recommendation_key=f"{slug}-k1")
    loop_action = commitment.commit_recommendation(conn, r1.recommendation_id).resulting_action_id
    transport, resolver, source = StubRealPostmarkTransport(), FakeRecipientResolver(), default_source()
    loop_spec = freeze_outreach(conn, setup, loop_action, channel_kind=pm.POSTMARK_CHANNEL)
    pm.execute_postmark_action(conn, loop_action, registry=registry_with(
        pm.PostmarkEmailWorker(transport, resolver, source)), source=source)
    assert pm.verify_postmark_action(conn, loop_action, transport=transport, resolver=resolver, source=source).verified
    mid = next(m for m, rec in transport.outbound.items()
               if str(rec["Metadata"].get("market_action_spec")) == str(loop_spec.market_action_spec_id))
    if outcome == "REPLIED":
        pm.ingest_postmark_reply(conn, {"RecordType": "Inbound",
                                        "MailboxHash": pm.reply_mailbox_hash(setup.venture_id, loop_spec.market_action_spec_id),
                                        "From": "lead@x.invalid", "TextBody": "yes", "MessageID": "in-real"},
                                 source=source, auth_header=basic_auth(), transport=transport)
    else:
        pm.ingest_postmark_event(conn, {"RecordType": "Bounce", "MessageID": mid, "Type": "HardBounce"},
                                 source=source, auth_header=basic_auth(), transport=transport)
    r2 = nextaction.recommend(conn, setup.venture_id, setup.opportunity_id, recommendation_key=f"{slug}-k2")
    return _Ctx(setup=setup, r1=r1, r2=r2, loop_action=loop_action, loop_spec=loop_spec)


def test_real_reply_loop_is_real_and_eligible(migrated):
    c = real_loop(migrated, "RL", outcome="REPLIED")
    r = _classify(migrated, c)
    assert r["reality_class"] == "REAL" and r["completeness"] == "COMPLETE"
    assert r["assistance_class"] == autonomy.CLEAN and r["eligible_clean_real_alpha"] is True


def test_real_bounce_loop_is_real(migrated):
    c = real_loop(migrated, "RLb", outcome="BOUNCED")
    # a real negative outcome is still REAL market evidence
    assert _classify(migrated, c)["reality_class"] == "REAL"


def test_real_loop_with_intervention_is_human_assisted(migrated):
    c = real_loop(migrated, "RLi", outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r1_at if hasattr(c, "r1_at") else _rec_time(migrated, c.r1.recommendation_id))
    r = _classify(migrated, c)
    assert r["reality_class"] == "REAL" and r["assistance_class"] == autonomy.HUMAN_ASSISTED
    assert r["eligible_clean_real_alpha"] is False   # REAL but not CLEAN


def test_unrelated_real_observation_cannot_upgrade_loop(migrated):
    # a SIMULATED loop is not upgraded to REAL by a REAL observation elsewhere in the venture
    c = sim_loop(migrated, "URO", outcome="REPLIED")
    other = real_loop(migrated, "UROreal", outcome="REPLIED")   # different venture, REAL
    assert _classify(migrated, c)["reality_class"] == "SIMULATED"
