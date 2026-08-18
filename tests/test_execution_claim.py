"""Execution authorization/claim: concurrency, single reservation, rechecks."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import psycopg
import pytest

from aidan_core import budget, execution, killswitch
from aidan_core.errors import ApprovalRequiredError, ExecutionBlockedError

from conftest import setup_action


def test_only_one_concurrent_claim_succeeds(migrated):
    _vid, aid = setup_action(migrated, slug="claim-1", autonomy_level=1, amount=40)
    url = os.environ["DATABASE_URL"]

    def worker():
        conn = psycopg.connect(url, autocommit=True)
        try:
            execution.authorize_and_claim(conn, aid, safety_mode="IDEMPOTENT")
            return "ok"
        except ExecutionBlockedError:
            return "blocked"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _: worker(), range(2)))

    assert results == ["blocked", "ok"]
    assert execution.get_status(migrated, aid) == "RUNNING"
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM execution_attempt WHERE action_request_id = %s AND status = 'CLAIMED'",
            (aid,),
        )
        assert cur.fetchone()[0] == 1


def test_reservation_happens_exactly_once(migrated):
    vid, aid = setup_action(migrated, slug="claim-2", autonomy_level=1, amount=40)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    assert budget.get_account(migrated, vid, "USD")[2] == Decimal("40.0000")
    # Re-claim blocked (already RUNNING); reservation unchanged.
    with pytest.raises(ExecutionBlockedError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    assert budget.get_account(migrated, vid, "USD")[2] == Decimal("40.0000")


def test_kill_switch_rechecked_at_execution(migrated):
    _vid, aid = setup_action(migrated, slug="claim-3", autonomy_level=1, amount=10)
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")


def test_approval_rechecked_at_execution(migrated):
    # Requires approval (autonomy) but none granted.
    _vid, aid = setup_action(migrated, slug="claim-4", autonomy_level=0, required_autonomy=2, amount=10)
    execution.request_execution(migrated, aid)
    with pytest.raises(ApprovalRequiredError):
        execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
