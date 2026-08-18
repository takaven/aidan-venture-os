"""Venture creation and append-only Venture Mandate versions.

Gate 1 stores only the durable *reference* primitive for a mandate (a version
number plus a content/reference hash). Mandate authoring, generation and
interpretation are out of scope. Mandate versions are immutable at the DB level
(see ``migrations/0002_*``); this module never overwrites an existing version.
"""
from __future__ import annotations

from typing import Optional

from . import audit, db


def create_venture(conn, slug: str, *, autonomy_level: int = 0) -> str:
    """Create a venture at lifecycle DISCOVERED. Returns its id (uuid)."""
    if not slug:
        raise ValueError("slug is required")
    with db.transaction(conn) as cur:
        cur.execute(
            """
            INSERT INTO venture (slug, autonomy_level)
            VALUES (%s, %s)
            RETURNING id
            """,
            (slug, autonomy_level),
        )
        venture_id = cur.fetchone()[0]
        audit.record_event(
            cur,
            event_type="venture.created",
            actor="system",
            venture_id=venture_id,
            payload={"slug": slug, "autonomy_level": autonomy_level},
        )
    return venture_id


def append_mandate_version(
    conn,
    venture_id: str,
    *,
    content_hash: str,
    source_ref: Optional[str] = None,
    actor: str = "board",
) -> int:
    """Append the next mandate version and designate it current.

    Version numbers are monotonic per venture; the ``(venture_id, version)``
    unique constraint is the ultimate guard against overwriting an existing
    version. Returns the new version number.
    """
    if not content_hash:
        raise ValueError("content_hash is required")
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT coalesce(max(version), 0) + 1 FROM venture_mandate_version "
            "WHERE venture_id = %s",
            (venture_id,),
        )
        version = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO venture_mandate_version (venture_id, version, content_hash, source_ref)
            VALUES (%s, %s, %s, %s)
            """,
            (venture_id, version, content_hash, source_ref),
        )
        cur.execute(
            "UPDATE venture SET mandate_version = %s, updated_at = now() WHERE id = %s",
            (version, venture_id),
        )
        audit.record_event(
            cur,
            event_type="venture.mandate_version_appended",
            actor=actor,
            venture_id=venture_id,
            payload={"version": version, "content_hash": content_hash},
        )
    return version


def get_current_mandate_version(conn, venture_id: str):
    """Return the latest mandate version row for a venture, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, venture_id, version, content_hash, source_ref, created_at
            FROM venture_mandate_version
            WHERE venture_id = %s
            ORDER BY version DESC
            LIMIT 1
            """,
            (venture_id,),
        )
        return cur.fetchone()


def get_venture(conn, venture_id: str):
    """Return the venture row, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, slug, lifecycle_state, mandate_version, autonomy_level,
                   created_at, updated_at
            FROM venture
            WHERE id = %s
            """,
            (venture_id,),
        )
        return cur.fetchone()
