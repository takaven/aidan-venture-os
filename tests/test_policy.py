"""Policy engine: pure precedence/determinism + persistence/immutability."""
from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from aidan_core import actions, decisions, killswitch, policy, ventures
from aidan_core.policy import PolicyInput


def _inp(**kw) -> PolicyInput:
    base = dict(
        action_type="probe",
        venture_autonomy=1,
        required_autonomy=0,
        global_kill=False,
        venture_kill=False,
        available_budget=Decimal("100"),
        requested_amount=Decimal("10"),
        currency="USD",
        approval_threshold=Decimal("1000"),
    )
    base.update(kw)
    return PolicyInput(**base)


# --------------------------------------------------------------------------
# Pure (no database).
# --------------------------------------------------------------------------
def test_determinism_same_inputs_same_decision_and_hash():
    a = policy.evaluate(_inp())
    b = policy.evaluate(_inp())
    assert a == b
    assert a.inputs_hash == b.inputs_hash


def test_precedence_and_outcomes():
    assert policy.evaluate(_inp()).decision == "ALLOW"
    assert policy.evaluate(_inp(global_kill=True)).reason == "KILL_SWITCH_GLOBAL"
    assert policy.evaluate(_inp(venture_kill=True)).reason == "KILL_SWITCH_VENTURE"
    assert policy.evaluate(_inp(requested_amount=Decimal("500"), available_budget=Decimal("100"))).reason == "INSUFFICIENT_BUDGET"
    assert policy.evaluate(_inp(required_autonomy=5)).reason == "AUTONOMY_INSUFFICIENT"
    assert policy.evaluate(
        _inp(requested_amount=Decimal("1500"), available_budget=Decimal("5000"))
    ).reason == "ABOVE_APPROVAL_THRESHOLD"


def test_precedence_is_ordered():
    # global kill beats everything.
    assert policy.evaluate(
        _inp(global_kill=True, venture_kill=True, requested_amount=Decimal("9999"), required_autonomy=9)
    ).reason == "KILL_SWITCH_GLOBAL"
    # venture kill beats budget.
    assert policy.evaluate(
        _inp(venture_kill=True, requested_amount=Decimal("9999"), available_budget=Decimal("0"))
    ).reason == "KILL_SWITCH_VENTURE"
    # budget beats autonomy.
    assert policy.evaluate(
        _inp(requested_amount=Decimal("9999"), available_budget=Decimal("0"), required_autonomy=9)
    ).reason == "INSUFFICIENT_BUDGET"


def test_no_public_save_api():
    # The only persistence path is evaluate_and_persist (which computes the outcome).
    assert not hasattr(policy, "save_policy_decision")


# --------------------------------------------------------------------------
# Integration (PostgreSQL).
# --------------------------------------------------------------------------
def _action(migrated, vid, *, key="k", amount=0, autonomy=0, currency="USD"):
    return actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a",
        idempotency_key=key, required_autonomy=autonomy,
        requested_amount=amount, requested_currency=currency,
    ).action_id


def test_persist_creates_immutable_record_and_audit(migrated):
    from aidan_core import budget

    vid = ventures.create_venture(migrated, slug="pol-1", autonomy_level=1)
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    aid = _action(migrated, vid, amount=10)

    result, did = policy.evaluate_and_persist(migrated, aid)
    assert result.decision == "ALLOW"

    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM policy_decision WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM audit_event WHERE action_id = %s AND event_type = 'policy.evaluated'",
            (aid,),
        )
        assert cur.fetchone()[0] == 1

    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE policy_decision SET reason = 'x' WHERE id = %s", (did,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM policy_decision WHERE id = %s", (did,))


def test_global_kill_forces_deny_cannot_be_bypassed(migrated):
    vid = ventures.create_venture(migrated, slug="pol-2", autonomy_level=5)
    aid = _action(migrated, vid, amount=0)
    killswitch.engage_global(migrated, engaged_by="op", reason="halt")

    result, _ = policy.evaluate_and_persist(migrated, aid)
    assert result.decision == "DENY"
    assert result.reason == "KILL_SWITCH_GLOBAL"


def test_policy_is_decide_only_no_state_mutation(migrated):
    vid = ventures.create_venture(migrated, slug="pol-3", autonomy_level=1)
    aid = _action(migrated, vid, amount=0)
    policy.evaluate_and_persist(migrated, aid)

    # Lifecycle unchanged, no investment decision, action status untouched.
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    assert decisions.get_decisions(migrated, vid) == []
    assert actions.get_action_request(migrated, aid)[7] == "PENDING"
