"""Gate 8 — PHASE-COMPOSED full-lifecycle joined Alpha (deterministic; no provider/network/send).

Proves one continuous run from ONLY a Venture Mandate through the real governed authorities to a
classified market loop: mandate -> run_research -> opportunity + Kill Case + assumption -> governed
validation (WTP + acquisition PASS) -> nextaction.recommend=BUILD -> commit(BUILD) -> real build
authority (build_spec against the committed BUILD action) -> Gate-5 quality PASS -> governed lifecycle
transition -> deploy authority -> deterministic deploy verification -> proof-gated promotion to
OPERATING -> MARKET recommendation -> committed MARKET action -> simulated execution + verification ->
observation -> next recommendation -> next committed decision -> classify_loop.

The test is the external CALLER that sequences the governed phases (the architecture deliberately
introduces no whole-venture orchestrator — ADR-025/026). It never directly writes canonical
lifecycle/validation/decision/proof/observation state that a production API is responsible for; every
causal stage arises through the real API. Local `fake-local` channel => reality_class SIMULATED. No
fabricated BUILD authority helper is used (the committed BUILD decision itself authorizes the build).
"""
from __future__ import annotations

import os

import psycopg
import pytest

from aidan_core import actions, budget, commitment, lifecycle, nextaction, validation, ventures
from aidan_core.alpha import autonomy, loop
from aidan_core.build import quality as build_quality
from aidan_core.build import repository as build_repo
from aidan_core.build import runtime as build_runtime
from aidan_core.build import spec as build_spec_mod
from aidan_core.deploy import release as deploy_release
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import state as deploy_state
from aidan_core.deploy import target as deploy_target
from aidan_core.errors import MarketAuthorityError, RecommendationNotConvertibleError
from aidan_core.market import runtime as market_runtime
from aidan_core.market.observation import record_market_observation
from aidan_core.research import orchestration, sources

from build_fakes import DEFAULT_INTENT, GOOD_CANDIDATE, GOOD_PRODUCT_MANIFEST, BuilderWorker, make_substrate
from deploy_fakes import DeployBundleWorker
from factory_fakes import registry_with
from market_fakes import ChannelWorker, MarketSetup, freeze_outreach
from research_fixtures import ReplayAdapter, ScriptedProposer, acquired, build_credible

_Q = "How burdensome is SMB reconciliation and will teams pay to automate it?"
_SRC = "SMB teams spend 5+ hours on reconciliation weekly."
_MANDATE = "MANDATE: build value for SMB finance teams."
_TECH = {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}
_BUILD_CAPS = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]


# --------------------------------------------------------------------------
# causal phase drivers (production APIs only; test plays the external caller)
# --------------------------------------------------------------------------
def _mandate_research(migrated, slug):
    """ONLY a Venture Mandate -> one real research run -> CANDIDATE opportunity + HIGH assumption."""
    vid = ventures.create_venture(migrated, slug=slug, autonomy_level=1)
    ventures.append_mandate_version(migrated, vid, content_hash=sources.content_hash(_MANDATE))
    r = orchestration.run_research(
        migrated, venture_id=vid, mandate_version=1, mandate_content=_MANDATE, run_key="rr",
        adapter=ReplayAdapter({_Q: [acquired(_SRC, key="s1")]}), proposer=ScriptedProposer([_Q], build_credible))
    opp = next(oid for oid, st in r.opportunity_statuses.items() if st == "CANDIDATE")
    return vid, opp, r.assumption_ids[0]


def _resolve_validation_for_build(migrated, vid, opp, assumption_id):
    """A validation authority records the two contexts the BUILD gate requires (WTP + acquisition),
    the WTP test also resolving the HIGH assumption — all through real validation APIs."""
    hw = validation.create_hypothesis(migrated, vid, opportunity_id=opp, assumption_id=assumption_id,
                                      statement="buyers will pay", hypothesis_key="h-wtp")
    tw = validation.create_test(migrated, vid, validation_hypothesis_id=hw.hypothesis_id, test_key="t-wtp",
                                test_type="PRICING", method="LOI", success_criterion=">=1 signed LOI",
                                evidence_required="LOI docs", success_metric="lois",
                                success_comparator="GTE", success_threshold=1, max_spend=100)
    validation.record_result(migrated, validation_test_id=tw.test_id, result_key="r-wtp",
                             observed_value={"lois": 1}, wtp_modality="SIGNED_COMMITMENT")
    ha = validation.create_hypothesis(migrated, vid, opportunity_id=opp,
                                      statement="reachable via outreach", hypothesis_key="h-acq")
    ta = validation.create_test(migrated, vid, validation_hypothesis_id=ha.hypothesis_id, test_key="t-acq",
                                test_type="OUTREACH", method="cold email", success_criterion=">=5 replies",
                                evidence_required="reply logs", success_metric="replies",
                                success_comparator="GTE", success_threshold=5, max_spend=100)
    validation.record_result(migrated, validation_test_id=ta.test_id, result_key="r-acq",
                             observed_value={"replies": 5}, measurement_kind="OUTREACH_RESPONSE")


def _commit_build(migrated, vid, opp):
    budget.grant_budget(migrated, vid, amount=100, currency="USD", granted_by="board")
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="rb")
    assert rec.action_type == "BUILD"                              # allocator selects BUILD, not defaulted
    res = commitment.commit_recommendation(migrated, rec.recommendation_id, requested_amount=10)
    assert res.decision == "BUILD" and res.policy_decision == "ALLOW" and res.resulting_action_id
    return rec, res


def _drive_build_to_quality_pass(migrated, slug, vid, opp, rec, res):
    """Freeze the build_spec against the REAL committed BUILD action, execute the builder, and reach a
    Gate-5 overall quality PASS — all governed APIs; no fabricated authority."""
    rel = make_substrate(migrated, key=f"rel-{slug}")
    fields = dict(DEFAULT_INTENT)
    fields["expected_output_contract"] = {"require": {"status": "done"}, "technical": dict(_TECH)}
    build_spec_mod.create_build_spec(
        migrated, res.resulting_action_id, source_investment_decision_id=res.decision_id,
        source_recommendation_id=rec.recommendation_id, opportunity_id=opp, **fields)
    build_repo.register_venture_repository(migrated, vid, repository_ref=f"venture://{slug}/app")
    worker = BuilderWorker(structured_output={
        "status": "done", "candidate_files": GOOD_CANDIDATE, "product_manifest": GOOD_PRODUCT_MANIFEST})
    build_runtime.execute_build(
        migrated, res.resulting_action_id, registry=registry_with(worker), worker_kind=worker.kind,
        verifier_kind="structured-contract", capability_scope=_BUILD_CAPS, timeout_seconds=60, max_attempts=1)
    cap = build_runtime.capture_and_check_build(migrated, res.resulting_action_id,
                                                substrate_release_id=rel.substrate_release_id)
    mid = cap["manifest"].build_manifest_id
    build_runtime.assess_build_quality(migrated, res.resulting_action_id, review_observations=())
    assert build_quality.overall_verdict(migrated, mid) == "PASS"
    return mid


def _drive_deploy_to_operating(migrated, slug, vid, mid):
    """Governed deploy authority + proof-gated promotion to OPERATING (mirrors run_deploy, real APIs)."""
    da = actions.submit_action_request(
        migrated, venture_id=vid, action_type="deploy", actor="a",
        idempotency_key=f"deploy:{slug}", requested_amount=0, requested_currency="USD").action_id
    target = deploy_target.register_deployment_target(
        migrated, vid, environment="staging", provider_kind="fake-a", target_ref=f"deploy://{slug}/staging")
    deploy_release.create_release_candidate(
        migrated, da, build_manifest_id=mid, deployment_target_id=target.target_id)
    lifecycle.transition(migrated, vid, "VALIDATING", actor="op")   # governed lifecycle authority
    lifecycle.transition(migrated, vid, "BUILDING", actor="op")
    dw = DeployBundleWorker(mode="compliant")
    deploy_runtime.execute_deploy(migrated, da, registry=registry_with(dw), worker_kind=dw.kind, max_attempts=1)
    deploy_runtime.verify_deploy(migrated, da, actual_cost=0)       # VERIFIED DEPLOYMENT_RELEASE proof
    deploy_state.promote_verified_deployment(migrated, da)          # proof-gated BUILDING -> OPERATING
    return da


def _mandate_to_operating(migrated, slug):
    vid, opp, a1 = _mandate_research(migrated, slug)
    _resolve_validation_for_build(migrated, vid, opp, a1)
    rec, res = _commit_build(migrated, vid, opp)
    mid = _drive_build_to_quality_pass(migrated, slug, vid, opp, rec, res)
    _drive_deploy_to_operating(migrated, slug, vid, mid)
    return vid, opp


def _market_loop(migrated, vid, opp):
    """From OPERATING: seed real market evidence, get MARKET recommendation, commit, execute+verify the
    committed action, observe, then the next committed decision (true loop exit). Returns (r1, r2)."""
    vh = validation.create_hypothesis(migrated, vid, opportunity_id=opp, statement="reachable",
                                      hypothesis_key="lh")
    vt = validation.create_test(migrated, vid, validation_hypothesis_id=vh.hypothesis_id, test_key="lt",
                                test_type="OUTREACH", method="cold email", success_criterion=">=5 replies",
                                evidence_required="logs", max_spend=100, max_duration_days=7,
                                success_metric="replies", success_comparator="GTE", success_threshold=5)
    setup = MarketSetup(vid, opp, vt.test_id)
    a_seed = actions.submit_action_request(migrated, venture_id=vid, action_type="send_outreach", actor="a",
                                           idempotency_key="mkt:seed", requested_amount=0).action_id
    seed_spec = freeze_outreach(migrated, setup, a_seed)
    market_runtime.execute_market_action(migrated, a_seed, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    market_runtime.verify_market_action(migrated, a_seed, actual_cost=0)
    record_market_observation(migrated, seed_spec.market_action_spec_id, external_event_id="seed",
                              observation_type="DELIVERED", channel_kind="fake-local")
    r1 = nextaction.recommend(migrated, vid, opp, recommendation_key="k1")
    assert r1.action_type == "MARKET"
    res1 = commitment.commit_recommendation(migrated, r1.recommendation_id)
    loop_action = res1.resulting_action_id
    loop_spec = freeze_outreach(migrated, setup, loop_action)
    market_runtime.execute_market_action(migrated, loop_action, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    market_runtime.verify_market_action(migrated, loop_action, actual_cost=0)
    record_market_observation(migrated, loop_spec.market_action_spec_id, external_event_id="o1",
                              observation_type="REPLIED", channel_kind="fake-local")
    r2 = nextaction.recommend(migrated, vid, opp, recommendation_key="k2")
    commitment.commit_recommendation(migrated, r2.recommendation_id)
    return r1, r2


# --------------------------------------------------------------------------
# the phase-composed joined Alpha run
# --------------------------------------------------------------------------
def test_full_join_mandate_to_classified_market_loop(migrated):
    vid, opp = _mandate_to_operating(migrated, "fj-main")

    # RESTART BOUNDARY: a fresh connection re-reads persisted canonical state (nothing memory-only)
    fresh = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        with fresh.cursor() as cur:
            cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
            assert cur.fetchone()[0] == "OPERATING"
    finally:
        fresh.close()

    r1, r2 = _market_loop(migrated, vid, opp)

    # clean autonomous market loop, correctly SIMULATED (local channel) and therefore not eligible-real
    c = loop.classify_loop(migrated, start_recommendation_id=r1.recommendation_id,
                           next_recommendation_id=r2.recommendation_id)
    assert c["completeness"] == "COMPLETE"
    assert c["assistance_class"] == autonomy.CLEAN
    assert c["reality_class"] == "SIMULATED" and c["eligible_clean_real_alpha"] is False

    # AUTONOMY: an unplanned intervention INSIDE the loop window flips the classification
    with migrated.cursor() as cur:
        cur.execute("SELECT created_at FROM next_action_recommendation WHERE id = %s", (r1.recommendation_id,))
        r1_at = cur.fetchone()[0]
    autonomy.record_intervention(migrated, vid, intervention_kind="REASONING_CORRECTION",
                                 intervention_stage="run", occurred_at=r1_at)
    c2 = loop.classify_loop(migrated, start_recommendation_id=r1.recommendation_id,
                            next_recommendation_id=r2.recommendation_id)
    assert c2["assistance_class"] == autonomy.HUMAN_ASSISTED and c2["eligible_clean_real_alpha"] is False

    # VENTURE ISOLATION: a next recommendation from another venture cannot close this loop
    other_vid, other_opp, _a = _mandate_research(migrated, "fj-other")
    other_rec = nextaction.recommend(migrated, other_vid, other_opp, recommendation_key="ok")
    with pytest.raises(MarketAuthorityError):
        loop.classify_loop(migrated, start_recommendation_id=r1.recommendation_id,
                           next_recommendation_id=other_rec.recommendation_id)


def test_full_join_uncommitted_next_is_incomplete(migrated):
    # the loop is COMPLETE only when the NEXT recommendation is actually committed (true exit)
    vid, opp = _mandate_to_operating(migrated, "fj-incomplete")
    # run the loop but DO NOT commit the next recommendation
    vh = validation.create_hypothesis(migrated, vid, opportunity_id=opp, statement="reachable", hypothesis_key="lh")
    vt = validation.create_test(migrated, vid, validation_hypothesis_id=vh.hypothesis_id, test_key="lt",
                                test_type="OUTREACH", method="cold email", success_criterion=">=5 replies",
                                evidence_required="logs", max_spend=100, max_duration_days=7,
                                success_metric="replies", success_comparator="GTE", success_threshold=5)
    setup = MarketSetup(vid, opp, vt.test_id)
    a_seed = actions.submit_action_request(migrated, venture_id=vid, action_type="send_outreach", actor="a",
                                           idempotency_key="mkt:seed", requested_amount=0).action_id
    seed_spec = freeze_outreach(migrated, setup, a_seed)
    market_runtime.execute_market_action(migrated, a_seed, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    market_runtime.verify_market_action(migrated, a_seed, actual_cost=0)
    record_market_observation(migrated, seed_spec.market_action_spec_id, external_event_id="seed",
                              observation_type="DELIVERED", channel_kind="fake-local")
    r1 = nextaction.recommend(migrated, vid, opp, recommendation_key="k1")
    res1 = commitment.commit_recommendation(migrated, r1.recommendation_id)
    loop_spec = freeze_outreach(migrated, setup, res1.resulting_action_id)
    market_runtime.execute_market_action(migrated, res1.resulting_action_id, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    market_runtime.verify_market_action(migrated, res1.resulting_action_id, actual_cost=0)
    record_market_observation(migrated, loop_spec.market_action_spec_id, external_event_id="o1",
                              observation_type="REPLIED", channel_kind="fake-local")
    r2 = nextaction.recommend(migrated, vid, opp, recommendation_key="k2")   # NOT committed
    c = loop.classify_loop(migrated, start_recommendation_id=r1.recommendation_id,
                           next_recommendation_id=r2.recommendation_id)
    assert c["completeness"] == "INCOMPLETE" and c["eligible_clean_real_alpha"] is False


def test_full_join_non_build_control(migrated):
    # anti-app-generator: mandate-origin, but WITHOUT authored validation the allocator does NOT
    # default to BUILD — it asks for more evidence, and that recommendation is non-convertible.
    vid, opp, _a = _mandate_research(migrated, "fj-ctrl")
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="c0")
    assert rec.action_type == "RESEARCH_MORE"
    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)
