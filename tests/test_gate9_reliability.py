"""Gate 9 Slice 1 — Reliability Fault Matrix (development acceptance suite).

Gate-9 exit principle under test:

    > failure does not require emergency manual reconstruction.

Method: ``claim -> cheapest falsification -> observed result``. Each test INDUCES a genuine
failure through existing PRODUCTION APIs + existing deterministic fakes and asserts the
*reliability* contract that a component test may not: the failure is durably recorded in
canonical PostgreSQL state, no false SUCCESS is created, recovery is bounded (or fails closed
to an explicit terminal/recovery state), no duplicate consequential effect occurs, and — the
Gate-9 distinguishing move — the permitted next step is reconstructable from canonical state
alone via a FRESH database connection (``_fresh()``), never from retained in-memory objects,
and never requires undocumented DB/state surgery.

These are truth-projection tests: they add no production code and assert only over existing
production behaviour (run first against UNCHANGED production per the cheapest-falsification rule).

Evidence matrix (claim | injected failure | expected canonical outcome | Gate-9 assertion):
  1  provider outage        | ambiguous external effect / hard error | RECOVERY_REQUIRED, no VERIFIED proof / bounded WORKER_ERROR | no blind re-issue; fail-closed reconstructable
  2  agent/worker failure   | worker raises / times out              | classified attempt, re-claimable or terminal FAILED       | retry bounded; terminal reconstructable, never SUCCESS
  3  bad deployment         | tampered (wrong-bytes) candidate        | verifier REJECTED, no DEPLOYMENT_RELEASE proof            | lifecycle stays BUILDING; no OPERATING promotion
  4  failed build           | required output missing + worker "pass" | technical/overall verdict FAIL                            | worker claim inert; FAIL durable
  5  contradictory research | SUPPORTS + CONTRADICTS on one claim     | claim_state DISPUTED (both retained)                      | survives restart; no fabricated reconciliation
  6  budget exhaustion      | reserve > grant / retry                 | InsufficientBudgetError; single RESERVE                   | no overspend; ledger invariant on fresh conn
  7  inconclusive experiment| unmet success criterion                | outcome INCONCLUSIVE (never auto-PASS)                    | no manufactured success; durable
  8  negative demand        | BOUNCED / UNSUBSCRIBE observation       | negative evidence retained                                | authors NO investment decision; absence not ingestible
  9  duplicate execution    | re-dispatch / replayed external id      | ExecutionBlockedError / IdempotencyConflictError          | one attempt, one VERIFIED proof, one effect — survives restart (fresh conn)
 10  restart                | crashed claim / process restart         | resume from durable state                                 | reconstructed from PostgreSQL alone via fresh conn
 --  venture isolation      | foreign source/channel identity         | MarketAuthorityError                                      | no cross-venture contamination; reconstructable via fresh conn
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from aidan_core import budget, commitment, execution, nextaction, ventures, validation
from aidan_core.build import quality as build_quality
from aidan_core.deploy import state as deploy_state
from aidan_core.errors import (
    AmbiguousExternalEffectError,
    DeployAuthorityError,
    EvidenceRelationConflictError,
    ExecutionBlockedError,
    IdempotencyConflictError,
    InsufficientBudgetError,
    MarketAuthorityError,
)
from aidan_core.factory import runtime
from aidan_core.market.observation import record_market_observation
from aidan_core.research import assumptions, claims, observations, opportunities, sources
from aidan_core.research.adapters import AcquiredSource

from build_fakes import build_authority, full_eval
from conftest import setup_action
from deploy_fakes import run_deploy
from factory_fakes import FakeWorkerA, FlakyWorker, registry_with, spec_action
from market_fakes import market_run

_UTC = timezone.utc
_DONE = {"require": {"status": "done"}}


# --------------------------------------------------------------------------
# Reconstruction helpers. ``migrated`` is autocommit, so committed canonical state
# is visible to an independent connection — the honest "process restart" probe.
# --------------------------------------------------------------------------
def _fresh():
    """A brand-new DB connection: canonical state only, no in-memory objects carried over."""
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _count(conn, sql, *args):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


def _verified_proofs(conn, aid):
    return _count(conn, "SELECT count(*) FROM proof_receipt WHERE action_request_id=%s AND result='VERIFIED'", aid)


def _attempts(conn, aid):
    return _count(conn, "SELECT count(*) FROM execution_attempt WHERE action_request_id=%s", aid)


def _lifecycle(conn, vid):
    return ventures.get_venture(conn, vid)[2]


class _AmbiguousWorker:
    """Models a provider outage AT the consequential boundary: the request may have taken
    effect but the confirming response was lost, so the effect is unconfirmable."""

    kind = "fake-a"

    def __init__(self):
        self.calls = 0

    def execute(self, request):  # no DB access — no authority
        self.calls += 1
        raise AmbiguousExternalEffectError("provider response lost; external effect is unconfirmed")


def _disputed_claim(conn, *, slug="g9-contra"):
    """Build one claim with genuinely contradictory evidence (SUPPORTS + CONTRADICTS)."""
    vid = ventures.create_venture(conn, slug=slug)

    def _src(loc, key, content):
        return sources.ingest(conn, vid, AcquiredSource(
            locator=loc, source_type="WEB_PAGE", content=content,
            retrieved_at=datetime(2026, 1, 1, tzinfo=_UTC), retrieved_by="a", acquisition_key=key,
        )).evidence_record_id

    oa = observations.create_observation(conn, vid, source_evidence_id=_src("https://a", "A", "A body"),
                                         statement="SMB WTP strong", observation_key="oa").evidence_record_id
    ob = observations.create_observation(conn, vid, source_evidence_id=_src("https://b", "B", "B body"),
                                         statement="SMB WTP weak", observation_key="ob").evidence_record_id
    cid = claims.create_claim(conn, vid, statement="SMB accounting teams have strong WTP", claim_key="c1").evidence_record_id
    claims.link_evidence(conn, claim_id=cid, observation_id=oa, stance="SUPPORTS")
    claims.link_evidence(conn, claim_id=cid, observation_id=ob, stance="CONTRADICTS")
    return vid, cid, oa, ob


# ==========================================================================
# 1. PROVIDER OUTAGE
# ==========================================================================
def test_mode1_ambiguous_provider_effect_fails_closed_no_blind_retry(migrated):
    # A consequential external effect became ambiguous: fail CLOSED, never blind-retry.
    vid, aid, sp = spec_action(migrated, "g9-outage-amb", max_attempts=3)
    r = runtime.execute_action(migrated, aid, registry=registry_with(_AmbiguousWorker()))
    assert r.failure_class == "AMBIGUOUS_EXTERNAL_EFFECT" and r.action_status == "RECOVERY_REQUIRED"
    assert _verified_proofs(migrated, aid) == 0  # no fabricated success on an unconfirmed effect
    # ordinary dispatch can NEVER blind-re-issue the effect (only explicit recovery may resolve it).
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(_AmbiguousWorker()))
    f = _fresh()
    try:  # a fresh process reads the exact permitted next step (explicit recovery) from PG alone.
        assert execution.get_status(f, aid) == "RECOVERY_REQUIRED"
        assert _verified_proofs(f, aid) == 0
    finally:
        f.close()


def test_mode1_deterministic_provider_error_bounded_no_false_success(migrated):
    # A deterministic hard error (provider rejects) is a bounded, classified failure; exhaustion
    # is an explicit terminal FAILED — never a fabricated success.
    vid, aid, sp = spec_action(migrated, "g9-outage-hard", max_attempts=1)
    from factory_fakes import ErrorWorker
    r = runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert r.failure_class == "RETRY_EXHAUSTED" and r.action_status == "FAILED"
    assert _verified_proofs(migrated, aid) == 0
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))


# ==========================================================================
# 2. AGENT / WORKER FAILURE
# ==========================================================================
def test_mode2_worker_error_retry_then_verified_success(migrated):
    # A retryable worker fault is NOT action failure while attempts remain; a later attempt
    # completes through the proof-gated success path exactly once.
    vid, aid, sp = spec_action(migrated, "g9-worker-retry", verifier_kind="structured-contract",
                               max_attempts=3, expected_output_contract=_DONE)
    reg = registry_with(FlakyWorker(fail_first=1, structured_output={"status": "done"}))
    r1 = runtime.execute_action(migrated, aid, registry=reg)
    assert r1.failure_class == "WORKER_ERROR" and r1.action_status == "PENDING"
    runtime.execute_action(migrated, aid, registry=reg)
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert _attempts(migrated, aid) == 2 and _verified_proofs(migrated, aid) == 1


def test_mode2_worker_failure_exhaustion_terminal_reconstructs(migrated):
    # Exhaustion is an explicit terminal FAILED; a fresh process reads that terminal truth
    # (no emergency reconstruction, no SUCCESS) directly from canonical state.
    from factory_fakes import ErrorWorker
    vid, aid, sp = spec_action(migrated, "g9-worker-exhaust", max_attempts=1)
    r = runtime.execute_action(migrated, aid, registry=registry_with(ErrorWorker()))
    assert r.action_status == "FAILED" and r.failure_class == "RETRY_EXHAUSTED"
    f = _fresh()
    try:
        assert execution.get_status(f, aid) == "FAILED"
        assert _verified_proofs(f, aid) == 0
        assert runtime.resume_action(f, aid, actual_cost=10)["outcome"] == "terminally_failed"
    finally:
        f.close()


# ==========================================================================
# 3. BAD DEPLOYMENT
# ==========================================================================
def test_mode3_bad_deployment_rejected_no_operating_promotion(migrated):
    run = run_deploy(migrated, "g9-deploy-bad", mode="wrong_bytes")   # tampered release bytes
    assert run.verify.verified is False                              # deterministic verifier rejects
    assert _verified_proofs(migrated, run.s.deploy_action_id) == 0
    with pytest.raises(DeployAuthorityError):                        # no VERIFIED release proof -> cannot promote
        deploy_state.promote_verified_deployment(migrated, run.s.deploy_action_id, actor="op")
    assert _lifecycle(migrated, run.s.venture_id) == "BUILDING"
    f = _fresh()
    try:
        assert _verified_proofs(f, run.s.deploy_action_id) == 0
        assert _lifecycle(f, run.s.venture_id) == "BUILDING"        # durable: never promoted to OPERATING
    finally:
        f.close()


# ==========================================================================
# 4. FAILED BUILD
# ==========================================================================
def test_mode4_failed_build_worker_claim_inert_no_false_qualification(migrated):
    # The worker DECLARES success and omits the required output; the deterministic kernel
    # verdict dominates and is FAIL — no path to a qualified build.
    ev = full_eval(migrated, "g9-build-fail",
                   candidate_files=[{"path": "app/other.py", "content": "x = 1\n"}],   # missing required app/main.py
                   worker_claims={"technical_pass": True, "quality_pass": True, "quality": "PASS"})
    assert ev.worker.calls == 1                     # the worker ran and CLAIMED success
    assert ev.technical == "FAIL" and ev.overall == "FAIL"
    f = _fresh()
    try:
        assert build_quality.overall_verdict(f, ev.manifest_id) == "FAIL"   # durable, reconstructable
    finally:
        f.close()


# ==========================================================================
# 5. CONTRADICTORY RESEARCH
# ==========================================================================
def test_mode5_contradiction_coexists_survives_restart(migrated):
    vid, cid, oa, ob = _disputed_claim(migrated)
    assert claims.claim_state(migrated, cid) == "DISPUTED"
    f = _fresh()
    try:  # reconstructed from PG alone: the contradiction is not lost across a restart.
        assert claims.claim_state(f, cid) == "DISPUTED"
        assert len(claims.provenance(f, cid)["paths"]) == 2
    finally:
        f.close()


def test_mode5_no_fabricated_reconciliation(migrated):
    # Re-asserting the opposite stance on the same evidence pair cannot silently "reconcile"
    # the contradiction away; the disputed truth is preserved.
    vid, cid, oa, ob = _disputed_claim(migrated, slug="g9-contra2")
    with pytest.raises(EvidenceRelationConflictError):
        claims.link_evidence(migrated, claim_id=cid, observation_id=ob, stance="SUPPORTS")
    assert claims.claim_state(migrated, cid) == "DISPUTED"


# ==========================================================================
# 6. BUDGET EXHAUSTION
# ==========================================================================
def test_mode6_over_grant_reservation_fails_closed_no_overspend(migrated):
    vid, aid = setup_action(migrated, slug="g9-budget-over", amount=100, grant=10, key="over")
    with pytest.raises(InsufficientBudgetError):
        budget.reserve_budget(migrated, aid)
    f = _fresh()
    try:  # no hidden reservation; ledger invariant intact and reconstructable.
        _id, granted, reserved, committed = budget.get_account(f, vid, "USD")
        assert (int(granted), int(reserved), int(committed)) == (10, 0, 0)
        assert _count(f, "SELECT count(*) FROM capital_entry WHERE action_request_id=%s AND entry_type='RESERVE'", aid) == 0
    finally:
        f.close()


def test_mode6_retry_does_not_double_reserve(migrated):
    vid, aid = setup_action(migrated, slug="g9-budget-fund", amount=10, grant=100, key="fund")
    assert budget.reserve_budget(migrated, aid) is True
    assert budget.reserve_budget(migrated, aid) is False   # idempotent: a retry reuses the same reservation
    assert _count(migrated, "SELECT count(*) FROM capital_entry WHERE action_request_id=%s AND entry_type='RESERVE'", aid) == 1


# ==========================================================================
# 7. INCONCLUSIVE EXPERIMENT
# ==========================================================================
def test_mode7_inconclusive_experiment_not_manufactured_success(migrated):
    auth = build_authority(migrated, slug="g9-inconclusive", key="i")
    h = validation.create_hypothesis(migrated, auth.venture_id, opportunity_id=auth.opportunity_id,
                                     statement="teams will pay to automate", hypothesis_key="h").hypothesis_id
    t = validation.create_test(migrated, auth.venture_id, validation_hypothesis_id=h, test_key="t",
                               test_type="INTERVIEW", method="m", success_criterion="score>=1",
                               evidence_required="notes", success_metric="score",
                               success_comparator="GTE", success_threshold=1).test_id
    res = validation.record_result(migrated, validation_test_id=t, result_key="r", observed_value={"score": 0})
    assert res.outcome == "INCONCLUSIVE"   # unmet criterion stays explicit uncertainty, never auto-PASS
    f = _fresh()
    try:
        assert _count(f, "SELECT count(*) FROM validation_result WHERE validation_test_id=%s AND outcome='PASS'", t) == 0
        assert _count(f, "SELECT count(*) FROM validation_result WHERE validation_test_id=%s AND outcome='INCONCLUSIVE'", t) == 1
    finally:
        f.close()


# ==========================================================================
# 8. NEGATIVE DEMAND
# ==========================================================================
def test_mode8_negative_demand_retained_without_investment_authority(migrated):
    r = market_run(migrated, "g9-neg")
    assert r.verify.verified is True
    spec_id = r.spec.market_action_spec_id
    before = _count(migrated, "SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", r.setup.venture_id)
    assert record_market_observation(migrated, spec_id, external_event_id="b1",
                                     observation_type="BOUNCED", channel_kind="fake-local").created is True
    record_market_observation(migrated, spec_id, external_event_id="u1",
                              observation_type="UNSUBSCRIBE", channel_kind="fake-local")
    with pytest.raises(MarketAuthorityError):   # absence is DERIVED, never an ingestible "no demand" event
        record_market_observation(migrated, spec_id, external_event_id="n1",
                                  observation_type="NO_RESPONSE", channel_kind="fake-local")
    after = _count(migrated, "SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", r.setup.venture_id)
    assert after == before   # negative evidence authored NO capital decision
    f = _fresh()
    try:
        assert _count(f, "SELECT count(*) FROM market_observation WHERE market_action_spec_id=%s", spec_id) == 2
        assert _count(f, "SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", r.setup.venture_id) == before
    finally:
        f.close()


# ==========================================================================
# 9. DUPLICATE EXECUTION
# ==========================================================================
def test_mode9_duplicate_execution_no_duplicate_effect(migrated):
    vid, aid, sp = spec_action(migrated, "g9-dup", verifier_kind="structured-contract",
                               expected_output_contract=_DONE)
    reg = registry_with(FakeWorkerA(structured_output={"status": "done"}))
    runtime.execute_action(migrated, aid, registry=reg)
    with pytest.raises(ExecutionBlockedError):   # duplicate dispatch while an attempt awaits verification
        runtime.execute_action(migrated, aid, registry=reg)
    assert _attempts(migrated, aid) == 1
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.verified is True and _verified_proofs(migrated, aid) == 1   # exactly one consequential proof
    # a replayed external identity with different content is a hard conflict, not a silent second effect.
    with pytest.raises(IdempotencyConflictError):
        execution.record_execution_result(migrated, aid, external_result_id=f"fake-a:{aid}:1",
                                          reported_outcome="success", raw_payload={"x": 999})

    # RESTART: a brand-new process (fresh connection, fresh worker/registry) cannot re-issue the
    # completed consequential effect — canonical state alone excludes the duplicate, from PostgreSQL.
    f = _fresh()
    try:
        assert execution.get_status(f, aid) == "SUCCEEDED"
        assert _attempts(f, aid) == 1 and _verified_proofs(f, aid) == 1
        fresh_worker = FakeWorkerA(structured_output={"status": "done"})   # never dispatched
        with pytest.raises(ExecutionBlockedError):
            runtime.execute_action(f, aid, registry=registry_with(fresh_worker))
        assert fresh_worker.calls == 0                                     # blocked before dispatch by canonical state
        with pytest.raises(IdempotencyConflictError):                     # replay conflict survives restart
            execution.record_execution_result(f, aid, external_result_id=f"fake-a:{aid}:1",
                                              reported_outcome="success", raw_payload={"x": 12345})
        assert _attempts(f, aid) == 1 and _verified_proofs(f, aid) == 1   # unchanged; no manual repair required
    finally:
        f.close()


# ==========================================================================
# 10. RESTART
# ==========================================================================
def test_mode10_restart_reconstructs_verification_from_postgres(migrated):
    # Worker ran and captured a durable result, then the process "crashes" before verification.
    vid, aid, sp = spec_action(migrated, "g9-restart", verifier_kind="structured-contract",
                               expected_output_contract=_DONE)
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    f = _fresh()
    try:  # a brand-new connection, no worker/registry: verification is reconstructed from PG alone.
        res = runtime.resume_action(f, aid, actual_cost=10)
        assert res["outcome"] == "verified_from_durable_state" and res["status"] == "SUCCEEDED"
        assert execution.get_status(f, aid) == "SUCCEEDED"
    finally:
        f.close()


def test_mode10_unsafe_crash_requires_explicit_recovery(migrated):
    # An UNSAFE attempt claimed and crashed mid-effect: recovery from a fresh process fails
    # closed to RECOVERY_REQUIRED (explicit governed recovery), never an auto-retry or DB surgery.
    vid, aid, sp = spec_action(migrated, "g9-crash-unsafe", max_attempts=3)
    execution.authorize_and_claim(migrated, aid, safety_mode="UNSAFE", lease_seconds=-1)
    f = _fresh()
    try:
        res = runtime.resume_action(f, aid, registry=registry_with(FakeWorkerA()), actual_cost=10)
        assert res["outcome"] == "recovery_required"
        assert execution.get_status(f, aid) == "RECOVERY_REQUIRED"
    finally:
        f.close()


# ==========================================================================
# CROSS-CUTTING: VENTURE ISOLATION (foreign evidence in a consequential path)
# ==========================================================================
def test_isolation_foreign_evidence_rejected_no_contamination(migrated):
    # Two independently OPERATING ventures, each with its own VERIFIED market action. Foreign
    # identity (another venture's source instance / a wrong channel) MUST NOT be able to attribute
    # a consequential market outcome to this venture's action — the reliability guarantee that a
    # failure/observation in one venture can never rewrite another's canonical truth.
    a = market_run(migrated, "g9-iso-a")
    b = market_run(migrated, "g9-iso-b")
    a_spec, b_spec = a.spec.market_action_spec_id, b.spec.market_action_spec_id
    # a legitimate, correctly-bound observation on A is accepted.
    assert record_market_observation(migrated, a_spec, external_event_id="a-ok",
                                     observation_type="DELIVERED", channel_kind="fake-local").created is True
    # foreign VENTURE's source instance cannot attribute an outcome to A's action.
    with pytest.raises(MarketAuthorityError):
        record_market_observation(migrated, a_spec, external_event_id="foreign-src",
                                  observation_type="DELIVERED", channel_kind="fake-local",
                                  source_instance_ref=f"fake-local:{b.setup.venture_id}")
    # foreign CHANNEL identity is likewise rejected.
    with pytest.raises(MarketAuthorityError):
        record_market_observation(migrated, a_spec, external_event_id="foreign-chan",
                                  observation_type="DELIVERED", channel_kind="other-channel")
    f = _fresh()
    try:  # neither venture's canonical truth was contaminated; reconstructable from PostgreSQL alone.
        assert _count(f, "SELECT count(*) FROM market_observation WHERE market_action_spec_id=%s", a_spec) == 1
        assert _count(f, "SELECT count(*) FROM market_observation WHERE market_action_spec_id=%s", b_spec) == 0
    finally:
        f.close()


# ==========================================================================
# SLICE-2 CLOSURE: durable uncertainty is CONSUMED by the allocator into a
# COMMITTED governed decision (legitimate authority; no manual reconciliation).
# These weld the durable-state proofs (modes 5/7 above) to nextaction.recommend
# -> commitment.commit_recommendation -> investment_decision_record.
# ==========================================================================
def _opp_with_assumption(conn, *, slug, importance):
    vid = ventures.create_venture(conn, slug=slug)
    opp = opportunities.create_opportunity(conn, vid, opportunity_key="o", buyer_hypothesis="B",
                                           problem_hypothesis="P", critical_unknown="U").opportunity_id
    aid = assumptions.create_assumption(conn, vid, proposition="p", assumption_key="a1",
                                        importance=importance, confidence="LOW",
                                        consequence_if_false="c", cheapest_test="t").assumption_id
    opportunities.link_assumption(conn, opportunity_id=opp, assumption_id=aid)
    hid = validation.create_hypothesis(conn, vid, opportunity_id=opp, statement="s",
                                       hypothesis_key="h", assumption_id=aid).hypothesis_id
    tid = validation.create_test(conn, vid, validation_hypothesis_id=hid, test_key="t",
                                 test_type="INTERVIEW", method="m", success_criterion="score>=1",
                                 evidence_required="notes", success_metric="score",
                                 success_comparator="GTE", success_threshold=1).test_id
    return vid, opp, tid


def _decision_count(conn, vid, rec_id):
    return _count(conn, "SELECT count(*) FROM investment_decision_record "
                        "WHERE venture_id=%s AND source_recommendation_id=%s", vid, rec_id)


def test_mode7_inconclusive_converts_to_committed_validate(migrated):
    # A materialized INCONCLUSIVE result (durable uncertainty) is consumed by the allocator into a
    # governed VALIDATE recommendation and COMMITTED to a canonical decision — no manual transcription.
    vid, opp, tid = _opp_with_assumption(migrated, slug="g9-inc-commit", importance="CRITICAL")
    res = validation.record_result(migrated, validation_test_id=tid, result_key="ri", observed_value={"score": 0})
    assert res.outcome == "INCONCLUSIVE"
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type == "VALIDATE"                     # uncertainty routed to the cheapest discriminating test
    cres = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert cres.decision == "VALIDATE"
    f = _fresh()
    try:  # the governed decision is reconstructable from PostgreSQL alone.
        assert _decision_count(f, vid, rec.recommendation_id) == 1
        assert _count(f, "SELECT count(*) FROM validation_result WHERE validation_test_id=%s "
                         "AND outcome='PASS'", tid) == 0     # no fabricated success carried into the decision
    finally:
        f.close()


def test_mode5_contradiction_consumed_into_committed_decision(migrated):
    # A preserved PASS+INCONCLUSIVE contradiction on a non-blocking assumption is consumed by the
    # allocator (VALIDATION_CONTRADICTORY) and COMMITTED to a governed decision, WITHOUT anyone first
    # manually reconciling the contradictory results (both remain preserved).
    vid, opp, tid = _opp_with_assumption(migrated, slug="g9-contra-commit", importance="MEDIUM")
    validation.record_result(migrated, validation_test_id=tid, result_key="rp", observed_value={"score": 2})  # PASS
    validation.record_result(migrated, validation_test_id=tid, result_key="ri", observed_value={"score": 0})  # INCONCLUSIVE
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r1")
    assert rec.action_type in ("VALIDATE", "HOLD") and rec.reason_code == "VALIDATION_CONTRADICTORY"
    cres = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert cres.decision == rec.action_type                  # legitimate governed conversion, not BUILD
    f = _fresh()
    try:
        assert _decision_count(f, vid, rec.recommendation_id) == 1
        assert _count(f, "SELECT count(*) FROM validation_result WHERE validation_test_id=%s", tid) == 2  # both retained
    finally:
        f.close()
