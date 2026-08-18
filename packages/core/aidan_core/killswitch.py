"""Kill switch: GLOBAL (singleton) and per-VENTURE scopes.

Global has precedence over venture (both cause a policy DENY). Every state
change is transactional and audited with who/why/when. Invalid scope/venture
combinations are rejected by the database (CHECK + partial unique indexes).
"""
from __future__ import annotations

from typing import Optional

from . import audit, db


def effective_state(cur, venture_id: str) -> dict:
    """Return ``{"global": bool, "venture": bool}`` using an existing cursor."""
    cur.execute("SELECT active FROM kill_switch WHERE scope = 'GLOBAL'")
    row = cur.fetchone()
    global_active = bool(row[0]) if row is not None else False

    cur.execute(
        "SELECT active FROM kill_switch WHERE scope = 'VENTURE' AND venture_id = %s",
        (venture_id,),
    )
    row = cur.fetchone()
    venture_active = bool(row[0]) if row is not None else False

    return {"global": global_active, "venture": venture_active}


def is_killed(conn, venture_id: str) -> bool:
    """True if the venture is effectively killed (global OR venture scope)."""
    with conn.cursor() as cur:
        state = effective_state(cur, venture_id)
    return state["global"] or state["venture"]


def _engage(conn, scope: str, venture_id: Optional[str], engaged_by: str, reason: Optional[str]) -> bool:
    with db.transaction(conn) as cur:
        if venture_id is None:
            cur.execute(
                "SELECT id, active FROM kill_switch WHERE scope = 'GLOBAL' FOR UPDATE"
            )
        else:
            cur.execute(
                "SELECT id, active FROM kill_switch WHERE scope = 'VENTURE' "
                "AND venture_id = %s FOR UPDATE",
                (venture_id,),
            )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                "INSERT INTO kill_switch (scope, venture_id, active, engaged_by, reason) "
                "VALUES (%s, %s, true, %s, %s)",
                (scope, venture_id, engaged_by, reason),
            )
        elif not row[1]:
            cur.execute(
                "UPDATE kill_switch SET active = true, engaged_by = %s, reason = %s, "
                "engaged_at = now(), released_by = NULL, released_at = NULL WHERE id = %s",
                (engaged_by, reason, row[0]),
            )
        else:
            return False  # already active -> idempotent, no audit

        audit.record_event(
            cur,
            event_type="killswitch.engaged",
            actor=engaged_by,
            venture_id=venture_id,
            payload={"scope": scope, "reason": reason},
        )
    return True


def _release(conn, scope: str, venture_id: Optional[str], released_by: str) -> bool:
    with db.transaction(conn) as cur:
        if venture_id is None:
            cur.execute(
                "SELECT id, active FROM kill_switch WHERE scope = 'GLOBAL' FOR UPDATE"
            )
        else:
            cur.execute(
                "SELECT id, active FROM kill_switch WHERE scope = 'VENTURE' "
                "AND venture_id = %s FOR UPDATE",
                (venture_id,),
            )
        row = cur.fetchone()
        if row is None or not row[1]:
            return False  # nothing active -> idempotent, no audit

        cur.execute(
            "UPDATE kill_switch SET active = false, released_by = %s, released_at = now() "
            "WHERE id = %s",
            (released_by, row[0]),
        )
        audit.record_event(
            cur,
            event_type="killswitch.released",
            actor=released_by,
            venture_id=venture_id,
            payload={"scope": scope},
        )
    return True


def engage_global(conn, *, engaged_by: str, reason: Optional[str] = None) -> bool:
    return _engage(conn, "GLOBAL", None, engaged_by, reason)


def release_global(conn, *, released_by: str) -> bool:
    return _release(conn, "GLOBAL", None, released_by)


def engage_venture(conn, venture_id: str, *, engaged_by: str, reason: Optional[str] = None) -> bool:
    if not venture_id:
        raise ValueError("venture_id is required for a VENTURE-scope kill switch")
    return _engage(conn, "VENTURE", venture_id, engaged_by, reason)


def release_venture(conn, venture_id: str, *, released_by: str) -> bool:
    if not venture_id:
        raise ValueError("venture_id is required for a VENTURE-scope kill switch")
    return _release(conn, "VENTURE", venture_id, released_by)
