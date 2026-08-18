"""Append-only Evidence Ledger primitive (Gate 1).

This is only a durable primitive so later gates have evidence-truth storage.
Interpretation is NOT evidence. There is no research automation, no source
adapters, no LLM synthesis and no scoring here.
"""
from __future__ import annotations

from typing import Any, Optional

from psycopg.types.json import Json

from . import audit, db

_KINDS = {"SOURCE", "OBSERVATION", "CLAIM"}


def record_evidence(
    conn,
    venture_id: str,
    *,
    kind: str,
    content_hash: str,
    action_request_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    actor: str = "evidence",
) -> str:
    """Append one evidence record. Returns its id."""
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {sorted(_KINDS)}")
    if not content_hash:
        raise ValueError("content_hash is required")
    with db.transaction(conn) as cur:
        cur.execute(
            """
            INSERT INTO evidence_record
                (venture_id, action_request_id, kind, source_ref, content_hash, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (venture_id, action_request_id, kind, source_ref, content_hash, Json(payload or {})),
        )
        evidence_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="evidence.recorded", actor=actor, venture_id=venture_id,
            action_id=action_request_id, payload={"kind": kind, "content_hash": content_hash},
        )
    return evidence_id


def get_evidence(conn, evidence_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, action_request_id, kind, source_ref, content_hash, "
            "payload, created_at FROM evidence_record WHERE id = %s",
            (evidence_id,),
        )
        return cur.fetchone()
