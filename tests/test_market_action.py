"""Gate 7 / Slice 1 — governed market action authority.

Proves: an OPERATING venture cannot execute a market action from prose; an immutable,
commercially-provenanced, venture-bound market_action_spec (exact channel/audience/
content/offer/spend) must be frozen and the generic Gate-4 execution_spec bound to it —
enforced at factory.spec.create_execution_spec for ANY caller — before a channel worker
(an ordinary WorkerAdapter) may be dispatched. No external send, observation, proof, or
interpretation. Commercial hypothesis/criteria/max_spend remain owned by validation_test.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import approvals, execution, killswitch
from aidan_core.errors import (
    ApprovalRequiredError,
    ExecutionBlockedError,
    IdempotencyConflictError,
    InconsistentCanonicalStateError,
    MarketAuthorityError,
)
from aidan_core.factory import runtime as factory_runtime
from aidan_core.factory import spec as spec_mod
from aidan_core.market import action as market_mod
from aidan_core.market import runtime as market_runtime
from aidan_core.market.runtime import MarketActionInput

from factory_fakes import FakeWorkerA, registry_with, spec_action
from market_fakes import OutreachWorker, OutreachWorkerB, freeze_outreach, market_action, operating_setup


def _spec(conn, aid, task_payload, caps=("SEND_OUTREACH",)):
    return spec_mod.create_execution_spec(
        conn, aid, worker_kind="outreach-a", verifier_kind="structured-contract",
        timeout_seconds=60, max_attempts=1, capability_scope=list(caps), task_payload=task_payload)


# ==========================================================================
# Provenance + OPERATING prerequisite (matrix 1-6, 47-48)
# ==========================================================================
def test_1_operating_freezes_spec(migrated):
    s = operating_setup(migrated, "m1")
    aid = market_action(migrated, s.venture_id, key="m1")
    res = freeze_outreach(migrated, s, aid)
    assert res.created is True and res.action_spec_hash and res.content_hash


def test_2_building_venture_rejected(migrated):
    # a venture that is not OPERATING cannot own a market action
    from deploy_fakes import run_deploy, to_building
    r = run_deploy(migrated, "m2")  # BUILDING (not promoted)
    with migrated.cursor() as cur:
        cur.execute("INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, statement, "
                    "statement_hash) VALUES (%s,'vh',%s,'s','h') RETURNING id",
                    (r.s.venture_id, r.s.eval.auth.opportunity_id))
        vh = cur.fetchone()[0]
        cur.execute("INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, method, "
                    "success_criterion, evidence_required, max_spend, definition_hash) "
                    "VALUES (%s,'vt',%s,'OUTREACH','m','sc','er','100','h') RETURNING id", (r.s.venture_id, vh))
        vt = cur.fetchone()[0]
    aid = market_action(migrated, r.s.venture_id, key="m2")
    with pytest.raises(MarketAuthorityError):
        market_mod.create_market_action_spec(migrated, aid, opportunity_id=r.s.eval.auth.opportunity_id,
                                              validation_test_id=vt, channel_kind="fake-local",
                                              audience_ref="a", content="hi")


def test_3_non_market_action_rejected(migrated):
    s = operating_setup(migrated, "m3")
    with migrated.cursor() as cur:
        cur.execute("INSERT INTO action_request (venture_id, action_type, actor, payload, payload_hash, "
                    "idempotency_key, required_autonomy, requested_amount, requested_currency) "
                    "VALUES (%s,'spend','a','{}'::jsonb,'h','sp:m3',0,0,'USD') RETURNING id", (s.venture_id,))
        spend_aid = cur.fetchone()[0]
    with pytest.raises(MarketAuthorityError):
        freeze_outreach(migrated, s, spend_aid)


def test_4_6_cross_venture_provenance_rejected(migrated):
    a = operating_setup(migrated, "m4a", key="m4a")
    b = operating_setup(migrated, "m4b", key="m4b")
    aid = market_action(migrated, a.venture_id, key="m4")
    # venture A's action with venture B's validation_test/opportunity
    with pytest.raises((InconsistentCanonicalStateError, psycopg.errors.ForeignKeyViolation, MarketAuthorityError)):
        market_mod.create_market_action_spec(migrated, aid, opportunity_id=b.opportunity_id,
                                              validation_test_id=b.validation_test_id, channel_kind="fake-local",
                                              audience_ref="a", content="hi")


def test_47_48_multiple_actions_same_validation_test_distinct_specs(migrated):
    s = operating_setup(migrated, "m47")
    a1 = market_action(migrated, s.venture_id, key="m47a")
    a2 = market_action(migrated, s.venture_id, key="m47b")
    r1 = freeze_outreach(migrated, s, a1, content="message one")
    r2 = freeze_outreach(migrated, s, a2, content="message two")
    assert r1.market_action_spec_id != r2.market_action_spec_id  # same validation_test, distinct specs


# ==========================================================================
# Exact content/offer/spend freeze + immutability (matrix 7-22)
# ==========================================================================
def test_7_8_9_content_hash_and_changed_content(migrated):
    s = operating_setup(migrated, "m7")
    a = market_action(migrated, s.venture_id, key="m7")
    first = freeze_outreach(migrated, s, a, content="exact body")
    again = freeze_outreach(migrated, s, a, content="exact body")
    assert again.created is False and again.content_hash == first.content_hash
    with pytest.raises(IdempotencyConflictError):
        freeze_outreach(migrated, s, a, content="different body")


@pytest.mark.parametrize("over", [
    dict(audience_ref="aud://OTHER"),
    dict(channel_kind="other-channel"),
    dict(offer_ref="offer-2"),
    dict(price_amount="9.99", price_currency="USD"),
])
def test_10_13_changed_binding_conflicts(migrated, over):
    s = operating_setup(migrated, "m10")
    a = market_action(migrated, s.venture_id, key="m10")
    freeze_outreach(migrated, s, a)
    with pytest.raises(IdempotencyConflictError):
        freeze_outreach(migrated, s, a, **over)


def test_16_17_19_validation_spend_bounds(migrated):
    s = operating_setup(migrated, "m16", max_spend="50")  # venture budget granted = 100
    # 16: authorized <= validation max accepted (amount must equal action requested_amount)
    a_ok = market_action(migrated, s.venture_id, key="m16ok", amount="40")
    assert freeze_outreach(migrated, s, a_ok, authorized_spend_amount="40").created is True
    # 17: authorized > validation max rejected
    a_over_vt = market_action(migrated, s.venture_id, key="m16vt", amount="60")
    with pytest.raises(MarketAuthorityError):
        freeze_outreach(migrated, s, a_over_vt, authorized_spend_amount="60")
    # 19: zero-spend accepted
    a_zero = market_action(migrated, s.venture_id, key="m16zero", amount="0")
    assert freeze_outreach(migrated, s, a_zero, authorized_spend_amount="0").created is True


def test_18_over_budget_rejected(migrated):
    # validation max is generous, but the authorized spend exceeds canonical available budget
    s = operating_setup(migrated, "m18", max_spend="1000")  # granted budget = 100
    a = market_action(migrated, s.venture_id, key="m18", amount="200")
    with pytest.raises(MarketAuthorityError):
        freeze_outreach(migrated, s, a, authorized_spend_amount="200")


def test_20_21_immutable_update_delete(migrated):
    s = operating_setup(migrated, "m20")
    a = market_action(migrated, s.venture_id, key="m20")
    res = freeze_outreach(migrated, s, a)
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE market_action_spec SET content = 'x' WHERE id = %s", (res.market_action_spec_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM market_action_spec WHERE id = %s", (res.market_action_spec_id,))


def test_45_46_no_duplicated_criteria_or_maxspend(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'market_action_spec' "
                    "AND column_name IN ('success_criterion','kill_criterion','max_spend','success_threshold')")
        assert cur.fetchone()[0] == 0


# ==========================================================================
# Canonical Factory market guard (matrix 23-31)
# ==========================================================================
def test_23_M1_no_spec_rejected(migrated):
    s = operating_setup(migrated, "m23")
    aid = market_action(migrated, s.venture_id, key="m23")  # market action, no spec
    with pytest.raises(MarketAuthorityError):
        _spec(migrated, aid, {"instruction": "send whatever message"})
    assert spec_mod.get_execution_spec(migrated, aid) is None


def test_24_M2_fabricated_binding_rejected(migrated):
    s = operating_setup(migrated, "m24")
    aid = market_action(migrated, s.venture_id, key="m24")
    with pytest.raises(MarketAuthorityError):
        _spec(migrated, aid, {"market": {"market_action_spec_id": "00000000-0000-0000-0000-000000000000",
                                         "action_spec_hash": "x", "channel_kind": "fake-local", "audience_ref": "a"}})


def test_25_28_M3_M4_M5_M6_binding_mismatches(migrated):
    s = operating_setup(migrated, "m25")
    a = market_action(migrated, s.venture_id, key="m25")
    ms = freeze_outreach(migrated, s, a)
    base = {"market_action_spec_id": str(ms.market_action_spec_id), "action_spec_hash": ms.action_spec_hash,
            "channel_kind": "fake-local", "audience_ref": "aud://segment-1"}
    for bad in (dict(base, action_spec_hash="wrong"),           # M3 wrong hash
                dict(base, audience_ref="aud://WRONG"),          # M4 wrong audience
                dict(base, channel_kind="wrong-channel")):       # M5 wrong channel
        with pytest.raises(MarketAuthorityError):
            _spec(migrated, a, {"market": bad})


def test_29_M7_exact_binding_accepted(migrated):
    s = operating_setup(migrated, "m29")
    a = market_action(migrated, s.venture_id, key="m29")
    freeze_outreach(migrated, s, a)
    dispatch = market_runtime.prepare_market_execution(migrated, a, worker_kind="outreach-a")
    block = spec_mod.get_execution_spec(migrated, a)[4]["market"]
    assert block["action_spec_hash"] == dispatch.action_spec_hash


def test_30_M8_non_market_action_unaffected(migrated):
    vid, aid, sp = spec_action(migrated, "m30", verifier_kind="structured-contract",
                               expected_output_contract={"require": {"status": "done"}})
    res = factory_runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    assert res.dispatched is True


# ==========================================================================
# Authorization chronology + kill (matrix 32-33, 39)
# ==========================================================================
def test_32_33_pre_spec_authorization_insufficient(migrated):
    s = operating_setup(migrated, "m32")
    aid = market_action(migrated, s.venture_id, key="m32", required_autonomy=2)  # REQUIRE_APPROVAL
    freeze_outreach(migrated, s, aid)
    pre = execution.request_execution(migrated, aid)
    approvals.approve(migrated, pre.approval_id, decided_by="board")
    w = OutreachWorker()
    with pytest.raises(ApprovalRequiredError):
        market_runtime.execute_market_action(migrated, aid, registry=registry_with(w), worker_kind="outreach-a")
    assert w.calls == 0
    fresh = factory_runtime.request_dispatch_authorization(migrated, aid)
    approvals.approve(migrated, fresh.approval_id, decided_by="board")
    _, res = market_runtime.execute_market_action(migrated, aid, registry=registry_with(w), worker_kind="outreach-a")
    assert res.dispatched is True and w.calls == 1


def test_39_kill_switch_blocks_dispatch(migrated):
    s = operating_setup(migrated, "m39")
    aid = market_action(migrated, s.venture_id, key="m39")
    freeze_outreach(migrated, s, aid)
    killswitch.engage_global(migrated, engaged_by="op")
    w = OutreachWorker()
    with pytest.raises(ExecutionBlockedError):
        market_runtime.execute_market_action(migrated, aid, registry=registry_with(w), worker_kind="outreach-a")
    assert w.calls == 0


# ==========================================================================
# Worker architecture + authority + no market truth (matrix 34-38, 40-44)
# ==========================================================================
def test_34_35_worker_uses_adapter_no_broaden(migrated):
    s = operating_setup(migrated, "m34")
    a = market_action(migrated, s.venture_id, key="m34")
    freeze_outreach(migrated, s, a)
    w = OutreachWorker()
    market_runtime.execute_market_action(migrated, a, registry=registry_with(w), worker_kind="outreach-a")
    assert w.calls == 1
    req = w.last_request
    assert not hasattr(req, "conn") and set(req.capabilities) == {"SEND_OUTREACH"}
    mi = MarketActionInput.from_worker_request(req)
    assert mi.channel_kind == "fake-local" and mi.content == "Hi — quick question about how your clinic handles pre-auth follow-ups?"


def test_36_38_40_44_worker_claims_inert_no_market_truth(migrated):
    s = operating_setup(migrated, "m36")
    a = market_action(migrated, s.venture_id, key="m36")
    freeze_outreach(migrated, s, a)
    w = OutreachWorker(structured_output={
        "sent": True, "replied": True, "qualified_reply": True, "payment_succeeded": True,
        "lifecycle": "ARCHIVED", "investment_decision": "SCALE"})
    market_runtime.execute_market_action(migrated, a, registry=registry_with(w), worker_kind="outreach-a")
    with migrated.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (s.venture_id,))
        assert cur.fetchone()[0] == "OPERATING"                       # worker lifecycle claim inert
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s AND decision = 'SCALE'",
                    (s.venture_id,))
        assert cur.fetchone()[0] == 0                                 # no investment decision
        # no Slice-2+ market schema exists yet
        cur.execute("SELECT to_regclass('public.market_observation'), to_regclass('public.market_interpretation')")
        assert cur.fetchone() == (None, None)
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s", (a,))
        assert cur.fetchone()[0] == 0                                 # no market proof semantics


def test_provider_neutral_two_channels(migrated):
    a = operating_setup(migrated, "mNa", key="mNa")
    aa = market_action(migrated, a.venture_id, key="mNa")
    freeze_outreach(migrated, a, aa)
    _, ra = market_runtime.execute_market_action(migrated, aa, registry=registry_with(OutreachWorker()),
                                                 worker_kind="outreach-a")
    b = operating_setup(migrated, "mNb", key="mNb")
    ab = market_action(migrated, b.venture_id, key="mNb")
    freeze_outreach(migrated, b, ab)
    _, rb = market_runtime.execute_market_action(migrated, ab, registry=registry_with(OutreachWorkerB()),
                                                 worker_kind="outreach-b")
    assert ra.dispatched and rb.dispatched


def test_capability_unknown_rejected(migrated):
    s = operating_setup(migrated, "mcap")
    a = market_action(migrated, s.venture_id, key="mcap")
    freeze_outreach(migrated, s, a)
    with pytest.raises(ValueError):
        _spec(migrated, a, {}, caps=("SET_MARKET_SUCCESS",))
