"""Append-only canonical audit/event primitive.

Immutability is enforced at the database level (triggers reject UPDATE, DELETE
and TRUNCATE on ``audit_event``); this module only writes and reads. Gate 1
does not implement a cryptographic hash chain — plain append-only immutability
is the requirement.
"""
from __future__ import annotations

from typing import Any, Optional

from psycopg.types.json import Json


def record_event(
    cur,
    *,
    event_type: str,
    actor: str,
    venture_id: Optional[str] = None,
    action_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> int:
    """Insert one audit event and return its id.

    ``cur`` is an open cursor whose transaction the caller controls.
    """
    if not event_type:
        raise ValueError("event_type is required")
    if not actor:
        raise ValueError("actor is required")
    cur.execute(
        """
        INSERT INTO audit_event (event_type, actor, venture_id, action_id, payload)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (event_type, actor, venture_id, action_id, Json(payload or {})),
    )
    return cur.fetchone()[0]


def get_event(cur, event_id: int):
    """Fetch one audit event row by id, or ``None``."""
    cur.execute(
        """
        SELECT id, event_type, actor, venture_id, action_id, payload, occurred_at
        FROM audit_event
        WHERE id = %s
        """,
        (event_id,),
    )
    return cur.fetchone()
