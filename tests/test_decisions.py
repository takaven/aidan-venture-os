"""Append-only investment decisions, independent of lifecycle."""
from __future__ import annotations

from aidan_core import decisions, ventures
from aidan_core.models import InvestmentDecision


def test_decision_records_and_appears_in_history(migrated):
    vid = ventures.create_venture(migrated, slug="dec-1")
    did = decisions.record_decision(
        migrated, vid, InvestmentDecision.VALIDATE, rationale_ref="ref-1"
    )
    history = decisions.get_decisions(migrated, vid)
    assert [h[0] for h in history] == [did]
    assert history[0][2] == "VALIDATE"


def test_decision_creates_audit_event(migrated):
    vid = ventures.create_venture(migrated, slug="dec-2")
    decisions.record_decision(migrated, vid, "HOLD")
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_event "
            "WHERE venture_id = %s AND event_type = 'investment_decision.recorded'",
            (vid,),
        )
        assert cur.fetchone()[0] == 1


def test_decision_does_not_mutate_lifecycle(migrated):
    vid = ventures.create_venture(migrated, slug="dec-3")
    before = ventures.get_venture(migrated, vid)[2]
    decisions.record_decision(migrated, vid, InvestmentDecision.KILL)
    after = ventures.get_venture(migrated, vid)[2]
    assert before == after == "DISCOVERED", "a KILL decision must not change lifecycle"
