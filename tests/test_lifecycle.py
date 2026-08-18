"""Guarded venture lifecycle transitions."""
from __future__ import annotations

import pytest

from aidan_core import lifecycle, ventures
from aidan_core.errors import IllegalTransitionError


def _audit_count(conn, venture_id, event_type):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_event WHERE venture_id = %s AND event_type = %s",
            (venture_id, event_type),
        )
        return cur.fetchone()[0]


def test_permitted_transition_succeeds_with_audit(migrated):
    vid = ventures.create_venture(migrated, slug="lc-ok")
    new_state = lifecycle.transition(migrated, vid, "VALIDATING", actor="tester")
    assert new_state == "VALIDATING"
    assert ventures.get_venture(migrated, vid)[2] == "VALIDATING"
    # Transition and its audit event committed together.
    assert _audit_count(migrated, vid, "venture.lifecycle_transition") == 1


def test_illegal_transition_rejected_leaves_state_and_audit_unchanged(migrated):
    vid = ventures.create_venture(migrated, slug="lc-bad")
    # DISCOVERED -> OPERATING is not permitted (skips states).
    with pytest.raises(IllegalTransitionError):
        lifecycle.transition(migrated, vid, "OPERATING", actor="tester")
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"  # unchanged
    # No false successful-transition audit event.
    assert _audit_count(migrated, vid, "venture.lifecycle_transition") == 0
