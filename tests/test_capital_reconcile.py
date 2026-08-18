"""Actual-cost reconciliation at canonical completion."""
from __future__ import annotations

from decimal import Decimal

import pytest

from aidan_core import budget, execution, proof
from aidan_core.errors import InsufficientBudgetError

from conftest import setup_action


def _claimed(conn, slug, *, amount, grant):
    vid, aid = setup_action(conn, slug=slug, autonomy_level=1, amount=amount, grant=grant)
    execution.authorize_and_claim(conn, aid, safety_mode="IDEMPOTENT")
    return vid, aid


def _complete(conn, aid, actual):
    return execution.complete_execution(
        conn, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": proof.expected_token(aid)}, actual_cost=actual,
    )


def test_reserved_greater_than_actual_releases_delta(migrated):
    vid, aid = _claimed(migrated, "rc-1", amount=50, grant=100)
    _complete(migrated, aid, 30)
    acct = budget.get_account(migrated, vid, "USD")
    assert acct[3] == Decimal("30.0000") and acct[2] == 0  # committed 30, reserved 0
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT amount FROM capital_entry WHERE action_request_id = %s AND entry_type = 'RELEASE'",
            (aid,),
        )
        assert cur.fetchone()[0] == Decimal("20.0000")


def test_actual_greater_than_reserved_within_budget(migrated):
    vid, aid = _claimed(migrated, "rc-2", amount=50, grant=100)
    _complete(migrated, aid, 80)
    acct = budget.get_account(migrated, vid, "USD")
    assert acct[3] == Decimal("80.0000") and acct[2] == 0


def test_actual_over_available_budget_rejected(migrated):
    vid, aid = _claimed(migrated, "rc-3", amount=50, grant=60)
    with pytest.raises(InsufficientBudgetError):
        _complete(migrated, aid, 80)
    # No partial success: reservation intact, nothing committed, still RUNNING, no proof.
    acct = budget.get_account(migrated, vid, "USD")
    assert acct[2] == Decimal("50.0000") and acct[3] == 0
    assert execution.get_status(migrated, aid) == "RUNNING"
    with migrated.cursor() as cur:
        assert proof.verified_proof_id(cur, aid) is None


def test_duplicate_completion_does_not_double_charge(migrated):
    vid, aid = _claimed(migrated, "rc-4", amount=50, grant=100)
    _complete(migrated, aid, 40)
    out2 = _complete(migrated, aid, 40)
    assert out2.duplicated is True
    assert budget.get_account(migrated, vid, "USD")[3] == Decimal("40.0000")
