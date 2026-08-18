"""Budget accounts and capital ledger: grants, reservations, invariants."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import psycopg
import pytest

from aidan_core import actions, budget, ventures
from aidan_core.errors import IllegalCapitalTransitionError, InsufficientBudgetError


def _action(conn, vid, *, key, amount, currency="USD"):
    return actions.submit_action_request(
        conn, venture_id=vid, action_type="spend", actor="a",
        idempotency_key=key, requested_amount=amount, requested_currency=currency,
    ).action_id


def test_grant_creates_balance_ledger_and_audit(migrated):
    vid = ventures.create_venture(migrated, slug="b-grant")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    acct = budget.get_account(migrated, vid, "USD")
    assert acct[1] == Decimal("100.0000") and acct[2] == 0 and acct[3] == 0
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM capital_entry WHERE venture_id = %s AND entry_type = 'GRANT'",
            (vid,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM audit_event WHERE event_type = 'budget.granted'")
        assert cur.fetchone()[0] == 1


def test_reserve_succeeds_and_insufficient_fails(migrated):
    vid = ventures.create_venture(migrated, slug="b-res")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    ok_action = _action(migrated, vid, key="a-ok", amount=40)
    assert budget.reserve_budget(migrated, ok_action) is True
    assert budget.get_account(migrated, vid, "USD")[2] == Decimal("40.0000")

    too_big = _action(migrated, vid, key="a-big", amount=1000)
    with pytest.raises(InsufficientBudgetError):
        budget.reserve_budget(migrated, too_big)


def test_duplicate_reservation_is_idempotent(migrated):
    vid = ventures.create_venture(migrated, slug="b-dup")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    aid = _action(migrated, vid, key="a", amount=30)
    assert budget.reserve_budget(migrated, aid) is True
    assert budget.reserve_budget(migrated, aid) is False  # no double reserve
    assert budget.get_account(migrated, vid, "USD")[2] == Decimal("30.0000")


def test_release_once_then_idempotent(migrated):
    vid = ventures.create_venture(migrated, slug="b-rel")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    aid = _action(migrated, vid, key="a", amount=30)
    budget.reserve_budget(migrated, aid)
    assert budget.release_budget(migrated, aid) is True
    assert budget.get_account(migrated, vid, "USD")[2] == 0
    assert budget.release_budget(migrated, aid) is False  # duplicate release harmless


def test_commit_once_then_idempotent(migrated):
    vid = ventures.create_venture(migrated, slug="b-com")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    aid = _action(migrated, vid, key="a", amount=30)
    budget.reserve_budget(migrated, aid)
    assert budget.commit_budget(migrated, aid) is True
    acct = budget.get_account(migrated, vid, "USD")
    assert acct[2] == 0 and acct[3] == Decimal("30.0000")
    assert budget.commit_budget(migrated, aid) is False  # duplicate commit harmless


def test_release_after_commit_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="b-rac")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    aid = _action(migrated, vid, key="a", amount=30)
    budget.reserve_budget(migrated, aid)
    budget.commit_budget(migrated, aid)
    with pytest.raises(IllegalCapitalTransitionError):
        budget.release_budget(migrated, aid)


def test_commit_after_release_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="b-car")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    aid = _action(migrated, vid, key="a", amount=30)
    budget.reserve_budget(migrated, aid)
    budget.release_budget(migrated, aid)
    with pytest.raises(IllegalCapitalTransitionError):
        budget.commit_budget(migrated, aid)


def test_ledger_is_immutable(migrated):
    vid = ventures.create_venture(migrated, slug="b-imm")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE capital_entry SET amount = 1 WHERE venture_id = %s", (vid,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM capital_entry WHERE venture_id = %s", (vid,))


def test_over_reservation_check_enforced_by_db(migrated):
    # Direct over-reservation must be rejected by the CHECK constraint.
    vid = ventures.create_venture(migrated, slug="b-check")
    budget.grant_budget(migrated, vid, amount=50, currency="USD")
    acct_id = budget.get_account(migrated, vid, "USD")[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "UPDATE budget_account SET reserved_amount = 999 WHERE id = %s", (acct_id,)
            )


def test_concurrent_reservations_cannot_exceed_grant(migrated):
    vid = ventures.create_venture(migrated, slug="b-conc")
    budget.grant_budget(migrated, vid, amount=100, currency="USD")
    a1 = _action(migrated, vid, key="c1", amount=60)
    a2 = _action(migrated, vid, key="c2", amount=60)
    url = os.environ["DATABASE_URL"]

    def worker(aid):
        conn = psycopg.connect(url, autocommit=True)
        try:
            budget.reserve_budget(conn, aid)
            return "ok"
        except InsufficientBudgetError:
            return "insufficient"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(worker, [a1, a2]))

    assert results == ["insufficient", "ok"]
    acct = budget.get_account(migrated, vid, "USD")
    assert acct[2] == Decimal("60.0000")  # exactly one reservation succeeded
    assert acct[2] + acct[3] <= acct[1]   # within grant
