"""Durable approvals: states, terminal enforcement, expiry, staleness."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import approvals, execution
from aidan_core.errors import ApprovalStateError

from conftest import setup_action


def _pending_approval(conn, *, ttl=3600):
    # required_autonomy 2 > autonomy_level 0 -> REQUIRE_APPROVAL
    vid, aid = setup_action(conn, slug=f"appr-{ttl}", autonomy_level=0, required_autonomy=2, amount=10)
    outcome = execution.request_execution(conn, aid, approval_ttl_seconds=ttl)
    assert outcome.decision == "REQUIRE_APPROVAL"
    return aid, outcome.approval_id


def test_pending_then_approve(migrated):
    _aid, approval_id = _pending_approval(migrated)
    assert approvals.get_approval(migrated, approval_id)[3] == "PENDING"
    assert approvals.approve(migrated, approval_id, decided_by="board") == "APPROVED"
    assert approvals.get_approval(migrated, approval_id)[3] == "APPROVED"


def test_duplicate_approve_is_idempotent(migrated):
    _aid, approval_id = _pending_approval(migrated)
    approvals.approve(migrated, approval_id, decided_by="board")
    assert approvals.approve(migrated, approval_id, decided_by="board") == "APPROVED"


def test_reject_then_terminal(migrated):
    _aid, approval_id = _pending_approval(migrated)
    assert approvals.reject(migrated, approval_id, decided_by="board") == "REJECTED"
    with pytest.raises(ApprovalStateError):
        approvals.approve(migrated, approval_id, decided_by="board")


def test_expire_pending(migrated):
    _aid, approval_id = _pending_approval(migrated, ttl=-1)  # already due
    assert approvals.expire_if_due(migrated, approval_id) == "EXPIRED"
    with pytest.raises(ApprovalStateError):
        approvals.approve(migrated, approval_id, decided_by="board")


def test_terminal_enforced_at_db_level(migrated):
    _aid, approval_id = _pending_approval(migrated)
    approvals.approve(migrated, approval_id, decided_by="board")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE approval SET state = 'REJECTED' WHERE id = %s", (approval_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM approval WHERE id = %s", (approval_id,))
