"""Append-only investment-decision records.

An investment decision (VALIDATE / BUILD / IMPROVE / MARKET / SCALE / HOLD /
KILL / DO_NOTHING) is recorded as a *decision*, distinct from venture lifecycle
state and from ActionRequest run status. Recording a decision never mutates the
venture's lifecycle. Decision intelligence and scoring are out of scope.
"""
from __future__ import annotations

from typing import Optional, Union

from . import audit, db
from .models import InvestmentDecision


def _as_value(decision: Union[str, InvestmentDecision]) -> str:
    return decision.value if isinstance(decision, InvestmentDecision) else str(decision)


def record_decision(
    conn,
    venture_id: str,
    decision: Union[str, InvestmentDecision],
    *,
    rationale_ref: Optional[str] = None,
    resulting_action_id: Optional[str] = None,
    actor: str = "aidan",
) -> str:
    """Append an investment decision. Returns its id. Does NOT touch lifecycle."""
    value = _as_value(decision)
    with db.transaction(conn) as cur:
        cur.execute(
            """
            INSERT INTO investment_decision_record
                (venture_id, decision, rationale_ref, resulting_action_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (venture_id, value, rationale_ref, resulting_action_id),
        )
        decision_id = cur.fetchone()[0]
        audit.record_event(
            cur,
            event_type="investment_decision.recorded",
            actor=actor,
            venture_id=venture_id,
            payload={"decision": value, "rationale_ref": rationale_ref},
        )
    return decision_id


def get_decisions(conn, venture_id: str) -> list:
    """Return a venture's decision history, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, venture_id, decision, rationale_ref, resulting_action_id, created_at
            FROM investment_decision_record
            WHERE venture_id = %s
            ORDER BY created_at, id
            """,
            (venture_id,),
        )
        return cur.fetchall()
