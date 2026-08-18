"""Kill switch: scopes, precedence, idempotency, audit, invalid combinations."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import killswitch, ventures


def _audit(conn, event_type):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_event WHERE event_type = %s", (event_type,))
        return cur.fetchone()[0]


def test_engage_and_release_global(migrated):
    vid = ventures.create_venture(migrated, slug="ks-g")
    assert killswitch.is_killed(migrated, vid) is False
    assert killswitch.engage_global(migrated, engaged_by="op", reason="halt") is True
    assert killswitch.is_killed(migrated, vid) is True
    # Idempotent re-engage does nothing.
    assert killswitch.engage_global(migrated, engaged_by="op") is False
    assert killswitch.release_global(migrated, released_by="op") is True
    assert killswitch.is_killed(migrated, vid) is False
    assert _audit(migrated, "killswitch.engaged") == 1
    assert _audit(migrated, "killswitch.released") == 1


def test_engage_and_release_venture(migrated):
    vid = ventures.create_venture(migrated, slug="ks-v")
    other = ventures.create_venture(migrated, slug="ks-v-other")
    assert killswitch.engage_venture(migrated, vid, engaged_by="op") is True
    assert killswitch.is_killed(migrated, vid) is True
    # Only the targeted venture is affected.
    assert killswitch.is_killed(migrated, other) is False
    assert killswitch.release_venture(migrated, vid, released_by="op") is True
    assert killswitch.is_killed(migrated, vid) is False


def test_global_has_precedence_over_venture(migrated):
    vid = ventures.create_venture(migrated, slug="ks-prec")
    killswitch.engage_global(migrated, engaged_by="op")
    # Even with no venture switch, global kills the venture.
    with migrated.cursor() as cur:
        state = killswitch.effective_state(cur, vid)
    assert state["global"] is True and state["venture"] is False
    assert killswitch.is_killed(migrated, vid) is True


def test_invalid_scope_combination_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="ks-bad")
    # GLOBAL scope must not carry a venture_id (CHECK constraint).
    with pytest.raises(psycopg.errors.CheckViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO kill_switch (scope, venture_id, engaged_by) "
                "VALUES ('GLOBAL', %s, 'op')",
                (vid,),
            )
    # VENTURE scope must carry a venture_id.
    with pytest.raises(psycopg.errors.CheckViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO kill_switch (scope, venture_id, engaged_by) "
                "VALUES ('VENTURE', NULL, 'op')"
            )
