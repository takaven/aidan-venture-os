"""Schema/domain-level proof that the three status concepts are separate.

This does not rely on Python enum identity; it exercises the actual columns:
each column accepts only its own vocabulary and rejects the others', and the
three concepts coexist with independent values on one venture.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import actions, decisions, ventures
from aidan_core.models import InvestmentDecision


def test_columns_reject_foreign_vocabulary(migrated):
    vid = ventures.create_venture(migrated, slug="sep-1")
    res = actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a", idempotency_key="k1"
    )

    # venture.lifecycle_state rejects a run_status label and a decision label.
    for bad in ("PENDING", "KILL"):
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            with migrated.cursor() as cur:
                cur.execute(
                    "UPDATE venture SET lifecycle_state = %s WHERE id = %s", (bad, vid)
                )

    # action_request.status rejects a lifecycle label and a decision label.
    for bad in ("BUILDING", "KILL"):
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            with migrated.cursor() as cur:
                cur.execute(
                    "UPDATE action_request SET status = %s WHERE id = %s",
                    (bad, res.action_id),
                )

    # investment_decision_record.decision rejects a lifecycle label.
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO investment_decision_record (venture_id, decision) "
                "VALUES (%s, %s)",
                (vid, "BUILDING"),
            )


def test_three_concepts_coexist_independently(migrated):
    vid = ventures.create_venture(migrated, slug="sep-2")
    res = actions.submit_action_request(
        migrated, venture_id=vid, action_type="probe", actor="a", idempotency_key="k1"
    )
    decisions.record_decision(migrated, vid, InvestmentDecision.KILL)

    with migrated.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        lifecycle_state = cur.fetchone()[0]
        cur.execute("SELECT status FROM action_request WHERE id = %s", (res.action_id,))
        run_status = cur.fetchone()[0]
        cur.execute(
            "SELECT decision FROM investment_decision_record WHERE venture_id = %s", (vid,)
        )
        decision = cur.fetchone()[0]

    # Three distinct concepts, distinct values, coexisting on one venture.
    assert lifecycle_state == "DISCOVERED"
    assert run_status == "PENDING"
    assert decision == "KILL"
    assert len({lifecycle_state, run_status, decision}) == 3
