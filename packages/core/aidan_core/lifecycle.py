"""Guarded venture lifecycle transitions.

There is exactly one canonical path to change a venture's lifecycle state:
:func:`transition`. It enforces an explicit permitted-transition set, locks the
venture row, and writes the state change and its audit event in one
transaction. Illegal transitions raise and leave lifecycle and audit history
unchanged. There is deliberately no generic "set lifecycle_state" method.

Vocabulary is the Slice 1 ``lifecycle_state`` enum only; this module does not
amend it.
"""
from __future__ import annotations

from typing import Union

from . import audit, db
from .errors import IllegalTransitionError, NotFoundError
from .models import LifecycleState

# Smallest coherent permitted set over the existing vocabulary.
# ARCHIVED is terminal. Early archival is allowed from any non-terminal state.
PERMITTED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("DISCOVERED", "VALIDATING"),
        ("VALIDATING", "BUILDING"),
        ("BUILDING", "OPERATING"),
        ("OPERATING", "ARCHIVED"),
        ("DISCOVERED", "ARCHIVED"),
        ("VALIDATING", "ARCHIVED"),
        ("BUILDING", "ARCHIVED"),
    }
)


def _as_value(state: Union[str, LifecycleState]) -> str:
    return state.value if isinstance(state, LifecycleState) else str(state)


def transition(
    conn,
    venture_id: str,
    to_state: Union[str, LifecycleState],
    *,
    actor: str,
    reason: Union[str, None] = None,
) -> str:
    """Move a venture to ``to_state`` if the transition is permitted.

    Returns the new state. Raises :class:`IllegalTransitionError` (leaving all
    state unchanged) if the transition is not in the permitted set, and
    :class:`NotFoundError` if the venture does not exist.
    """
    target = _as_value(to_state)
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT lifecycle_state FROM venture WHERE id = %s FOR UPDATE",
            (venture_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"venture {venture_id} does not exist")
        current = row[0]

        if (current, target) not in PERMITTED_TRANSITIONS:
            raise IllegalTransitionError(
                f"transition {current} -> {target} is not permitted"
            )

        cur.execute(
            "UPDATE venture SET lifecycle_state = %s, updated_at = now() WHERE id = %s",
            (target, venture_id),
        )
        audit.record_event(
            cur,
            event_type="venture.lifecycle_transition",
            actor=actor,
            venture_id=venture_id,
            payload={"from": current, "to": target, "reason": reason},
        )
    return target
