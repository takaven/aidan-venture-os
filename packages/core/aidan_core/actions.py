"""Canonical ActionRequest intake with PostgreSQL-enforced idempotency.

Slice 2 implements intake only: creating the durable, deduplicated record. No
policy, approval, execution, proof or budget logic exists here yet.

Idempotency rules for a given ``(venture_id, idempotency_key)``:
- first request creates exactly one ActionRequest and one creation audit event;
- an exact duplicate (same canonical payload) returns the existing request and
  creates no second row and no second creation event;
- a reuse with a *different* canonical payload is a hard, deterministic
  conflict (:class:`IdempotencyConflictError`).

Deduplication is enforced by the ``UNIQUE (venture_id, idempotency_key)``
constraint plus ``INSERT ... ON CONFLICT`` — never an in-memory pre-check — so
concurrent duplicate inserts still converge on one canonical row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from . import audit, db
from .errors import IdempotencyConflictError


def canonical_payload_hash(payload: Optional[dict[str, Any]]) -> str:
    """Deterministic SHA-256 of a JSON payload, insensitive to key ordering.

    The hash is for idempotency comparison and provenance only; it is not a
    claim of evidence truth. Suitable for the JSON-serialisable payload types
    used in Gate 1; it is intentionally not a general canonicalisation
    framework.
    """
    canonical = json.dumps(
        payload or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IntakeResult:
    """Outcome of :func:`submit_action_request`."""

    action_id: str
    created: bool  # True if a new row was created, False if an existing one was returned


def submit_action_request(
    conn,
    *,
    venture_id: str,
    action_type: str,
    actor: str,
    idempotency_key: str,
    payload: Optional[dict[str, Any]] = None,
    required_autonomy: int = 0,
    requested_amount: "Any" = 0,
    requested_currency: str = "USD",
) -> IntakeResult:
    """Idempotently intake an ActionRequest. See module docstring for rules.

    ``required_autonomy`` / ``requested_amount`` / ``requested_currency`` are the
    canonical policy/budget inputs (Slice 3); they default to a no-cost,
    lowest-autonomy action.
    """
    if not action_type:
        raise ValueError("action_type is required")
    if not idempotency_key:
        raise ValueError("idempotency_key is required")

    payload = payload or {}
    payload_hash = canonical_payload_hash(payload)

    with db.transaction(conn) as cur:
        cur.execute(
            """
            INSERT INTO action_request
                (venture_id, action_type, actor, payload, payload_hash, idempotency_key,
                 required_autonomy, requested_amount, requested_currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (venture_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (
                venture_id, action_type, actor, Json(payload), payload_hash, idempotency_key,
                required_autonomy, requested_amount, requested_currency,
            ),
        )
        row = cur.fetchone()

        if row is not None:
            action_id = row[0]
            audit.record_event(
                cur,
                event_type="action_request.created",
                actor=actor,
                venture_id=venture_id,
                action_id=action_id,
                payload={"action_type": action_type, "payload_hash": payload_hash},
            )
            return IntakeResult(action_id=action_id, created=True)

        # Conflict on the unique key: an ActionRequest already exists.
        cur.execute(
            "SELECT id, payload_hash FROM action_request "
            "WHERE venture_id = %s AND idempotency_key = %s",
            (venture_id, idempotency_key),
        )
        existing_id, existing_hash = cur.fetchone()
        if existing_hash != payload_hash:
            raise IdempotencyConflictError(
                f"idempotency key reused with a different payload for venture {venture_id}"
            )
        return IntakeResult(action_id=existing_id, created=False)


def get_action_request(conn, action_id: str):
    """Return an ActionRequest row, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, venture_id, action_type, actor, payload, payload_hash,
                   idempotency_key, status, created_at, updated_at
            FROM action_request
            WHERE id = %s
            """,
            (action_id,),
        )
        return cur.fetchone()
