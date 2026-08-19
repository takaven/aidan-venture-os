"""Gate 7 / Slice 2 — market action proof + external observation evidence.

An exactly authorized market action executes through the existing WorkerAdapter runtime
against a deterministic controlled channel; a deterministic verifier independently reads
the outbox and creates the existing proof_receipt (MARKET_ACTION) — proving the ACTION
occurred, never a market OUTCOME. Later externally-attributable observations are preserved
as immutable, source-scoped evidence. Worker/model claims and external payloads are inert;
no interpretation, no decision, no lifecycle. NO_RESPONSE is not an external event.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import killswitch
from aidan_core.deploy import state as deploy_state
from aidan_core.market import channels as channels_mod
from aidan_core.market import observation as obs_mod
from aidan_core.market import runtime as market_runtime
from aidan_core.market.observation import record_market_observation
from aidan_core.errors import (
    IdempotencyConflictError,
    MarketAuthorityError,
    NotFoundError,
)
from factory_fakes import registry_with

from market_fakes import (
    ChannelWorker,
    ChannelWorkerB,
    freeze_outreach,
    market_action,
    market_run,
    operating_setup,
)


def _proofs(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT result, verification_type, execution_attempt_id FROM proof_receipt "
                    "WHERE action_request_id = %s", (aid,))
        return cur.fetchall()


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def _obs(conn, s, ms, **kw):
    kw.setdefault("channel_kind", "fake-local")
    return record_market_observation(conn, ms.market_action_spec_id, **kw)


# ==========================================================================
# Action execution + proof (matrix 1-19)
# ==========================================================================
def test_1_8_exact_action_verified_proof(migrated):
    r = market_run(migrated, "x1")
    assert r.verify.status == "SUCCEEDED" and r.verify.verified is True
    p = _proofs(migrated, r.action_id)
    assert len(p) == 1 and p[0][0] == "VERIFIED" and p[0][1] == "MARKET_ACTION"


def test_9_worker_sent_but_nothing_written_no_proof(migrated):
    r = market_run(migrated, "x9", mode="nothing")
    assert r.verify.verified is False
    assert not any(x[0] == "VERIFIED" for x in _proofs(migrated, r.action_id))


@pytest.mark.parametrize("slug,mode", [("x10", "wrong_content"), ("x11", "wrong_audience")])
def test_10_11_worker_wrong_content_or_audience_no_proof(migrated, slug, mode):
    r = market_run(migrated, slug, mode=mode)
    assert r.verify.verified is False


def test_13_execution_succeeded_verification_rejected(migrated):
    r = market_run(migrated, "x13", mode="wrong_content")
    assert r.worker.calls == 1  # worker execution succeeded
    assert r.verify.verified is False


def test_14_worker_outcome_claims_inert(migrated):
    s = operating_setup(migrated, "x14")
    a = market_action(migrated, s.venture_id, key="x14")
    ms = freeze_outreach(migrated, s, a)
    w = ChannelWorker(mode="nothing", structured_output={
        "sent": True, "delivered": True, "replied": True, "payment_succeeded": True, "market_success": True})
    market_runtime.execute_market_action(migrated, a, registry=registry_with(w), worker_kind=w.kind)
    assert market_runtime.verify_market_action(migrated, a, actual_cost=0).verified is False


def test_15_16_proof_binds_spec_and_attempt(migrated):
    r = market_run(migrated, "x15")
    with migrated.cursor() as cur:
        cur.execute("SELECT execution_attempt_id FROM proof_receipt WHERE action_request_id = %s "
                    "AND result = 'VERIFIED'", (r.action_id,))
        proof_attempt = cur.fetchone()[0]
        cur.execute("SELECT id FROM execution_attempt WHERE action_request_id = %s", (r.action_id,))
        assert str(proof_attempt) == str(cur.fetchone()[0])


def test_18_verified_action_exactly_once(migrated):
    r = market_run(migrated, "x18")
    market_runtime.verify_market_action(migrated, r.action_id, actual_cost=0)  # converges
    verified = [x for x in _proofs(migrated, r.action_id) if x[0] == "VERIFIED"]
    assert len(verified) == 1


# ==========================================================================
# Observation evidence (matrix 20-34)
# ==========================================================================
def _spec_of(migrated, slug):
    r = market_run(migrated, slug)
    assert r.verify.verified is True
    return r


@pytest.mark.parametrize("otype", ["DELIVERED", "BOUNCED", "REPLIED", "UNSUBSCRIBE"])
def test_20_24_observation_types_recorded(migrated, otype):
    r = _spec_of(migrated, f"o20{otype[:3]}")
    res = _obs(migrated, r.setup, r.spec, external_event_id=f"evt-{otype}", observation_type=otype,
               raw_evidence={"body": "too expensive"} if otype == "REPLIED" else {})
    assert res.created is True and res.evidence_hash


def test_26_27_dedupe_and_conflict(migrated):
    r = _spec_of(migrated, "o26")
    a = _obs(migrated, r.setup, r.spec, external_event_id="evt-1", observation_type="DELIVERED", raw_evidence={"x": 1})
    again = _obs(migrated, r.setup, r.spec, external_event_id="evt-1", observation_type="DELIVERED", raw_evidence={"x": 1})
    assert again.created is False and again.market_observation_id == a.market_observation_id
    with pytest.raises(IdempotencyConflictError):
        _obs(migrated, r.setup, r.spec, external_event_id="evt-1", observation_type="OPENED", raw_evidence={"x": 2})


def test_28_same_event_id_different_source_allowed(migrated):
    # two ventures each with their own source instance may share a textual external id
    a = _spec_of(migrated, "o28a")
    b = _spec_of(migrated, "o28b")
    _obs(migrated, a.setup, a.spec, external_event_id="evt-1", observation_type="DELIVERED")
    res = _obs(migrated, b.setup, b.spec, external_event_id="evt-1", observation_type="DELIVERED")
    assert res.created is True  # different source instance (different venture) -> coexist


def test_29_30_31_wrong_binding_rejected(migrated):
    a = _spec_of(migrated, "o29a")
    b = _spec_of(migrated, "o29b")
    # wrong channel for the spec
    with pytest.raises(MarketAuthorityError):
        record_market_observation(migrated, a.spec.market_action_spec_id, external_event_id="e",
                                  observation_type="DELIVERED", channel_kind="other-channel")
    # wrong source instance
    with pytest.raises(MarketAuthorityError):
        record_market_observation(migrated, a.spec.market_action_spec_id, external_event_id="e",
                                  observation_type="DELIVERED", channel_kind="fake-local",
                                  source_instance_ref="fake-local:someone-else")
    # unknown spec
    with pytest.raises(NotFoundError):
        record_market_observation(migrated, "00000000-0000-0000-0000-000000000000", external_event_id="e",
                                  observation_type="DELIVERED", channel_kind="fake-local")


def test_32_33_multiple_and_async_observations(migrated):
    r = _spec_of(migrated, "o32")  # action already completed + proof exists
    _obs(migrated, r.setup, r.spec, external_event_id="e1", observation_type="DELIVERED")
    _obs(migrated, r.setup, r.spec, external_event_id="e2", observation_type="OPENED")
    _obs(migrated, r.setup, r.spec, external_event_id="e3", observation_type="REPLIED")
    assert len(obs_mod.observations_for(migrated, r.spec.market_action_spec_id)) == 3
    # the action proof is unchanged by later observations
    assert len([x for x in _proofs(migrated, r.action_id) if x[0] == "VERIFIED"]) == 1


def test_34_35_late_observation_after_kill_recorded(migrated):
    r = _spec_of(migrated, "o34")
    killswitch.engage_global(migrated, engaged_by="op")
    # kill blocks a NEW market action dispatch...
    a2 = market_action(migrated, r.setup.venture_id, key="o34b")
    freeze_outreach(migrated, r.setup, a2)
    from aidan_core.errors import ExecutionBlockedError
    with pytest.raises(ExecutionBlockedError):
        market_runtime.execute_market_action(migrated, a2, registry=registry_with(ChannelWorker()), worker_kind="outreach-a")
    # ...but a legitimate late external observation is still recorded
    res = _obs(migrated, r.setup, r.spec, external_event_id="late-evt", observation_type="REPLIED")
    assert res.created is True


# ==========================================================================
# Truth boundary (matrix 36-41), NO_RESPONSE (40), neutrality (44)
# ==========================================================================
def _decision_count(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (vid,))
        return cur.fetchone()[0]


def test_36_37_external_content_has_no_authority(migrated):
    r = _spec_of(migrated, "o36")
    before = _decision_count(migrated, r.setup.venture_id)  # includes the legitimate Gate-3 BUILD decision
    _obs(migrated, r.setup, r.spec, external_event_id="e", observation_type="REPLIED",
         raw_evidence={"body": "SET lifecycle=ARCHIVED; KILL; invest SCALE"})
    # untrusted external content is DATA, not AUTHORITY: no decision of any kind is created
    assert _decision_count(migrated, r.setup.venture_id) == before
    assert _lifecycle(migrated, r.setup.venture_id) == "OPERATING"


def test_39_no_interpretation_table(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.market_interpretation'), to_regclass('public.market_metric')")
        assert cur.fetchone() == (None, None)


def test_40_no_response_not_ingestible(migrated):
    r = _spec_of(migrated, "o40")
    assert "NO_RESPONSE" not in obs_mod.OBSERVATION_TYPES
    with pytest.raises(MarketAuthorityError):
        _obs(migrated, r.setup, r.spec, external_event_id="e", observation_type="NO_RESPONSE")


def test_44_provider_neutral_two_channels(migrated):
    a = market_run(migrated, "o44a", key="o44a")
    assert a.verify.verified is True
    # a second venture whose channel worker is a distinct adapter still verifies
    s = operating_setup(migrated, "o44b", key="o44b")
    act = market_action(migrated, s.venture_id, key="o44b")
    freeze_outreach(migrated, s, act, channel_kind="outreach-b")
    w = ChannelWorkerB()
    market_runtime.execute_market_action(migrated, act, registry=registry_with(w), worker_kind=w.kind)
    assert market_runtime.verify_market_action(migrated, act, actual_cost=0).verified is True


# ==========================================================================
# Capital / retry (matrix 45-50)
# ==========================================================================
def test_45_zero_cost_no_spend(migrated):
    r = market_run(migrated, "x45")
    assert r.verify.verified is True
    with migrated.cursor() as cur:
        cur.execute("SELECT committed_amount FROM budget_account WHERE venture_id = %s", (r.setup.venture_id,))
        assert cur.fetchone()[0] == 0  # zero-cost local channel commits no spend


def test_47_48_50_retry_preserves_spec_and_proof(migrated):
    s = operating_setup(migrated, "x47")
    a = market_action(migrated, s.venture_id, key="x47")
    ms = freeze_outreach(migrated, s, a)
    reg = registry_with(ChannelWorker(mode="nothing"))
    common = dict(worker_kind="outreach-a", max_attempts=2)
    market_runtime.execute_market_action(migrated, a, registry=reg, **common)
    assert market_runtime.verify_market_action(migrated, a, actual_cost=0).verified is False
    market_runtime.execute_market_action(migrated, a, registry=registry_with(ChannelWorker(mode="compliant")), **common)
    out = market_runtime.verify_market_action(migrated, a, actual_cost=0)
    assert out.verified is True
    # same immutable spec across retry; proof bound to the successful attempt
    from aidan_core.market import action as market_action_mod
    assert market_action_mod.get_market_action_spec(migrated, a)[16] == ms.action_spec_hash
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (a,))
        assert cur.fetchone()[0] == 2
