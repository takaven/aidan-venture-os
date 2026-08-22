"""Gate 9 Slice 3 — FRESH held-out reliability attack suite.

Authored AFTER the Gate-9 development freeze (commit 7a6b35b; production tree identical to the
canonical base 19bfb78). These held-outs attack the FROZEN implementation, not restate the
development matrix: every mode varies at least one material dimension from
``tests/test_gate9_reliability.py`` — failure timing, retry count, fake/adapter, failure
classification, restart point, evidence combination, venture identity, result ordering, deployment
defect, budget timing, or allocator-continuation path.

Each held-out supports the canonical Gate-9 claim — *failure does not require emergency manual
reconstruction* — asserting over canonical PostgreSQL state, with fresh-connection reconstruction
where the mode's principle is durability/restart. Expected outcomes are authored independently
(never derived by calling the function under test). Existing production APIs + deterministic fakes
only; no provider/network calls.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from aidan_core import actions, budget, commitment, execution, nextaction, ventures, validation
from aidan_core.build import quality as build_quality
from aidan_core.deploy import state as deploy_state
from aidan_core.errors import (
    AmbiguousExternalEffectError,
    DeployAuthorityError,
    ExecutionBlockedError,
    IdempotencyConflictError,
    InsufficientBudgetError,
    MarketAuthorityError,
    RecommendationNotConvertibleError,
)
from aidan_core.factory import runtime
from aidan_core.market.observation import record_market_observation
from aidan_core.research import assumptions, opportunities

from build_fakes import GOOD_CANDIDATE, full_eval
from conftest import setup_action
from deploy_fakes import run_deploy
from factory_fakes import FakeClock, FakeWorkerA, SlowWorker, registry_with, spec_action
from market_fakes import market_run

_DONE = {"require": {"status": "done"}}


def _fresh():
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
    kind = "fake-a"

    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise AmbiguousExternalEffectError("provider accepted then connection dropped; effect unconfirmed")


def _opp_assumption(conn, *, slug, importance):
    vid = ventures.create_venture(conn, slug=slug)
    opp = opportunities.create_opportunity(conn, vid, opportunity_key="o", buyer_hypothesis="B",
                                           problem_hypothesis="P", critical_unknown="U").opportunity_id
    aid = assumptions.create_assumption(conn, vid, proposition="p", assumption_key="a1",
                                        importance=importance, confidence="LOW",
                                        consequence_if_false="c", cheapest_test="t").assumption_id
    opportunities.link_assumption(conn, opportunity_id=opp, assumption_id=aid)
    hid = validation.create_hypothesis(conn, vid, opportunity_id=opp, statement="s",
                                       hypothesis_key="h", assumption_id=aid).hypothesis_id
    return vid, opp, hid


# 1 PROVIDER OUTAGE — vary: max_attempts=1 (dev used 3); assert the PERSISTED DB failure_class
# column (RECOVERY_REQUIRED), and that a FRESH worker on a fresh connection is never dispatched.
def test_ho_mode1_ambiguous_effect_single_attempt_persisted_class(migrated):
    vid, aid, sp = spec_action(migrated, "ho-outage", max_attempts=1)
    r = runtime.execute_action(migrated, aid, registry=registry_with(_AmbiguousWorker()))
    assert r.action_status == "RECOVERY_REQUIRED"
    f = _fresh()
    try:
        assert _count(f, "SELECT count(*) FROM execution_attempt WHERE action_request_id=%s "
                         "AND failure_class='RECOVERY_REQUIRED'", aid) == 1
        assert _verified_proofs(f, aid) == 0
        fresh_worker = _AmbiguousWorker()
        with pytest.raises(ExecutionBlockedError):
            runtime.execute_action(f, aid, registry=registry_with(fresh_worker))
        assert fresh_worker.calls == 0   # canonical RECOVERY_REQUIRED blocks re-issue before dispatch
    finally:
        f.close()


# 2 WORKER FAILURE — vary: TIMEOUT classification (dev used WORKER_ERROR), retry-then-succeed at
# max_attempts=2; the timed-out attempt captured NO result so it can never become success.
def test_ho_mode2_timeout_retry_then_success(migrated):
    vid, aid, sp = spec_action(migrated, "ho-timeout", verifier_kind="structured-contract",
                               timeout=5, max_attempts=2, expected_output_contract=_DONE)
    clock = FakeClock()
    r1 = runtime.execute_action(
        migrated, aid, registry=registry_with(SlowWorker(clock, 10, structured_output={"status": "done"})), clock=clock)
    assert r1.failure_class == "TIMEOUT" and r1.action_status == "PENDING"
    assert _count(migrated, "SELECT count(*) FROM execution_result WHERE action_request_id=%s", aid) == 0
    runtime.execute_action(
        migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})), clock=clock)
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    f = _fresh()
    try:
        assert execution.get_status(f, aid) == "SUCCEEDED" and _verified_proofs(f, aid) == 1
    finally:
        f.close()


# 3 BAD DEPLOYMENT — vary: unhealthy deploy (dev used tampered wrong-bytes). Also assert the prior
# BUILD quality remains PASS, so a fresh process can tell a deploy failure from a build failure.
def test_ho_mode3_unhealthy_deploy_rejected_quality_preserved(migrated):
    run = run_deploy(migrated, "ho-deploy", mode="no_health")
    assert run.verify.verified is False
    assert run.s.eval.overall == "PASS"          # deploy failure did not corrupt build quality
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, run.s.deploy_action_id, actor="op")
    f = _fresh()
    try:
        assert _verified_proofs(f, run.s.deploy_action_id) == 0
        assert _lifecycle(f, run.s.venture_id) == "BUILDING"
    finally:
        f.close()


# 4 FAILED BUILD — vary: a DIFFERENT technical contract (a different required output missing) while
# the worker claims technical+quality pass. Kernel verdict FAIL dominates; manifest identity retained.
def test_ho_mode4_wrong_required_file_worker_claims_pass_inert(migrated):
    ev = full_eval(
        migrated, "ho-build",
        contract_extra={"technical": {"required_files": ["app/service.py"], "forbidden_files": [],
                                      "required_commands": ["pytest"]}},
        candidate_files=GOOD_CANDIDATE,          # provides app/main.py, NOT app/service.py
        worker_claims={"technical_pass": True, "quality_pass": True})
    assert ev.worker.calls == 1 and ev.technical == "FAIL" and ev.overall == "FAIL"
    assert ev.manifest_id is not None            # failed attempt's manifest identity is captured
    f = _fresh()
    try:
        assert build_quality.overall_verdict(f, ev.manifest_id) == "FAIL"
    finally:
        f.close()


# 5 CONTRADICTORY RESEARCH — vary: result ordering reversed (INCONCLUSIVE before PASS) and a
# different venture; still consumed into a committed governed decision (never BUILD).
def test_ho_mode5_contradiction_reversed_order_committed(migrated):
    vid, opp, hid = _opp_assumption(migrated, slug="ho-contra", importance="MEDIUM")
    tid = validation.create_test(migrated, vid, validation_hypothesis_id=hid, test_key="t",
                                 test_type="INTERVIEW", method="m", success_criterion="score>=1",
                                 evidence_required="notes", success_metric="score",
                                 success_comparator="GTE", success_threshold=1).test_id
    validation.record_result(migrated, validation_test_id=tid, result_key="ri", observed_value={"score": 0})  # INCONCLUSIVE first
    validation.record_result(migrated, validation_test_id=tid, result_key="rp", observed_value={"score": 2})  # PASS second
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r")
    assert rec.reason_code == "VALIDATION_CONTRADICTORY" and rec.action_type in ("VALIDATE", "HOLD")
    cres = commitment.commit_recommendation(migrated, rec.recommendation_id)
    assert cres.decision == rec.action_type
    f = _fresh()
    try:
        assert _count(f, "SELECT count(*) FROM investment_decision_record WHERE source_recommendation_id=%s",
                      rec.recommendation_id) == 1
        assert _count(f, "SELECT count(*) FROM validation_result WHERE validation_test_id=%s", tid) == 2
    finally:
        f.close()


# 6 BUDGET EXHAUSTION — vary: timing. A prior reservation consumes the whole grant, so a SECOND
# action in the same venture is denied (dev denied a single over-grant request). No overspend.
def test_ho_mode6_grant_exhausted_by_prior_reservation(migrated):
    vid, aid_a = setup_action(migrated, slug="ho-budget", amount=10, grant=10, key="A")
    b = actions.submit_action_request(migrated, venture_id=vid, action_type="spend", actor="a",
                                      idempotency_key="B", requested_amount=1, requested_currency="USD").action_id
    assert budget.reserve_budget(migrated, aid_a) is True    # consumes the whole grant
    with pytest.raises(InsufficientBudgetError):
        budget.reserve_budget(migrated, b)
    f = _fresh()
    try:
        _id, granted, reserved, committed = budget.get_account(f, vid, "USD")
        assert (int(granted), int(reserved), int(committed)) == (10, 10, 0)
        assert _count(f, "SELECT count(*) FROM capital_entry WHERE action_request_id=%s AND entry_type='RESERVE'", b) == 0
    finally:
        f.close()


# 7 INCONCLUSIVE EXPERIMENT — vary continuation path: an INCONCLUSIVE result on a NON-discriminating
# test yields RESEARCH_MORE, which is deliberately NON-convertible (the governed boundary refuses to
# fabricate a decision from insufficient evidence). Dev proved the INCONCLUSIVE->committed-VALIDATE path.
def test_ho_mode7_inconclusive_without_discriminating_test_nonconvertible(migrated):
    vid, opp, hid = _opp_assumption(migrated, slug="ho-inc", importance="CRITICAL")
    tid = validation.create_test(migrated, vid, validation_hypothesis_id=hid, test_key="t",
                                 test_type="INTERVIEW", method="m", success_criterion="subjective",
                                 evidence_required="notes").test_id   # non-discriminating (no structured criterion)
    res = validation.record_result(migrated, validation_test_id=tid, result_key="r", observed_value={"score": 0})
    assert res.outcome == "INCONCLUSIVE"
    rec = nextaction.recommend(migrated, vid, opp, recommendation_key="r")
    assert rec.action_type == "RESEARCH_MORE" and rec.reason_code == "INSUFFICIENT_EVIDENCE"
    with pytest.raises(RecommendationNotConvertibleError):
        commitment.commit_recommendation(migrated, rec.recommendation_id)
    f = _fresh()
    try:  # no governed decision was fabricated from the durable uncertainty.
        assert _count(f, "SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", vid) == 0
    finally:
        f.close()


# 8 NEGATIVE DEMAND — vary evidence combination: a negative UNSUBSCRIBE coexists with a positive
# DELIVERED (negative evidence is first-class and never overwritten), foreign-venture attribution is
# rejected, and neither authors an investment decision.
def test_ho_mode8_negative_first_class_and_isolated(migrated):
    r = market_run(migrated, "ho-neg")
    other = market_run(migrated, "ho-neg-other")
    spec = r.spec.market_action_spec_id
    before = _count(migrated, "SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", r.setup.venture_id)
    record_market_observation(migrated, spec, external_event_id="u", observation_type="UNSUBSCRIBE", channel_kind="fake-local")
    record_market_observation(migrated, spec, external_event_id="d", observation_type="DELIVERED", channel_kind="fake-local")
    with pytest.raises(MarketAuthorityError):   # foreign venture cannot attribute an outcome here
        record_market_observation(migrated, spec, external_event_id="f", observation_type="DELIVERED",
                                  channel_kind="fake-local", source_instance_ref=f"fake-local:{other.setup.venture_id}")
    assert _count(migrated, "SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", r.setup.venture_id) == before
    f = _fresh()
    try:
        assert _count(f, "SELECT count(*) FROM market_observation WHERE market_action_spec_id=%s "
                         "AND observation_type='UNSUBSCRIBE'", spec) == 1   # negative retained, not overwritten
        assert _count(f, "SELECT count(*) FROM market_observation WHERE market_action_spec_id=%s", spec) == 2
    finally:
        f.close()


# 9 DUPLICATE EXECUTION — vary mechanism: cross-attempt reuse of an external result identity is a hard
# conflict (dev attacked active-attempt re-dispatch + post-success replay). One durable effect remains.
def test_ho_mode9_cross_attempt_external_id_reuse_conflicts(migrated):
    vid, aid, sp = spec_action(migrated, "ho-dup", max_attempts=3)
    h1 = execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    execution.record_execution_result(migrated, aid, external_result_id="dup", reported_outcome="success",
                                      raw_payload={"a": 1}, attempt_id=h1.attempt_id)
    execution.fail_attempt(migrated, aid, attempt_id=h1.attempt_id, failure_class="WORKER_ERROR", terminal=False)
    h2 = execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    with pytest.raises(IdempotencyConflictError):
        execution.record_execution_result(migrated, aid, external_result_id="dup", reported_outcome="success",
                                          raw_payload={"a": 1}, attempt_id=h2.attempt_id)
    f = _fresh()
    try:
        assert _count(f, "SELECT count(*) FROM execution_result WHERE action_request_id=%s", aid) == 1  # one effect only
        assert _attempts(f, aid) == 2
    finally:
        f.close()


# 10 RESTART — vary restart point: an IDEMPOTENT attempt crashes BEFORE capturing any result; recovery
# from a FRESH connection dispatches a genuine new attempt and completes (dev proved verify-from-durable
# and UNSAFE->RECOVERY_REQUIRED). Reconstruction is from PostgreSQL alone.
def test_ho_mode10_idempotent_crash_recovers_and_dispatches(migrated):
    vid, aid, sp = spec_action(migrated, "ho-restart", verifier_kind="structured-contract",
                               max_attempts=2, expected_output_contract=_DONE)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT", lease_seconds=-1)  # crash, no result
    f = _fresh()
    try:
        worker = FakeWorkerA(structured_output={"status": "done"})
        res = runtime.resume_action(f, aid, registry=registry_with(worker), actual_cost=10)
        assert res["outcome"] == "recovered_and_dispatched" and worker.calls == 1
        out = runtime.verify_and_complete(f, aid, actual_cost=10)
        assert out.status == "SUCCEEDED" and out.verified is True and _verified_proofs(f, aid) == 1
    finally:
        f.close()
