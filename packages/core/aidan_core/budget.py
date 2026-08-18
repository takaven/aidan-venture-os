"""Budget accounts and the append-only capital ledger (Slice 3).

Operations (grant / reserve / release / commit) are transactional and audited,
and update the canonical balances. Correctness under concurrency comes from the
database, not application checks alone: the budget_account row is locked
(``FOR UPDATE``), a CHECK enforces ``reserved + committed <= granted`` and
non-negative balances, and partial unique indexes enforce one RESERVE / RELEASE
/ COMMIT per action. Reservation/release/commit are idempotent.

Actual-cost reconciliation is intentionally out of scope (Slice 4).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from . import audit, db
from .errors import IllegalCapitalTransitionError, InsufficientBudgetError, NotFoundError


def grant_budget(
    conn,
    venture_id: str,
    *,
    amount,
    currency: str,
    granted_by: str = "board",
    reference: Optional[str] = None,
) -> str:
    """Create or increase a venture's approved capacity. Returns account id."""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    with db.transaction(conn) as cur:
        cur.execute(
            """
            INSERT INTO budget_account (venture_id, currency, granted_amount)
            VALUES (%s, %s, %s)
            ON CONFLICT (venture_id, currency)
            DO UPDATE SET granted_amount = budget_account.granted_amount + EXCLUDED.granted_amount,
                          updated_at = now()
            RETURNING id
            """,
            (venture_id, currency, amount),
        )
        account_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO capital_entry
                (venture_id, budget_account_id, entry_type, amount, currency, reference)
            VALUES (%s, %s, 'GRANT', %s, %s, %s)
            """,
            (venture_id, account_id, amount, currency, reference),
        )
        audit.record_event(
            cur,
            event_type="budget.granted",
            actor=granted_by,
            venture_id=venture_id,
            payload={"amount": str(amount), "currency": currency},
        )
    return account_id


def _action_context(cur, action_request_id: str):
    cur.execute(
        "SELECT venture_id, requested_amount, requested_currency "
        "FROM action_request WHERE id = %s",
        (action_request_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"action_request {action_request_id} does not exist")
    return row  # (venture_id, requested_amount, requested_currency)


def _lock_account(cur, venture_id: str, currency: str):
    cur.execute(
        "SELECT id, granted_amount, reserved_amount, committed_amount "
        "FROM budget_account WHERE venture_id = %s AND currency = %s FOR UPDATE",
        (venture_id, currency),
    )
    return cur.fetchone()


def _capital_state(cur, action_request_id: str) -> dict:
    cur.execute(
        "SELECT entry_type, amount FROM capital_entry "
        "WHERE action_request_id = %s AND entry_type IN ('RESERVE', 'RELEASE', 'COMMIT')",
        (action_request_id,),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def _reserve(cur, action_request_id: str, *, actor: str = "budget") -> bool:
    """Cursor-based reservation (composes into a larger transaction)."""
    venture_id, amount, currency = _action_context(cur, action_request_id)
    amount = Decimal(amount)

    account = _lock_account(cur, venture_id, currency)
    if account is None:
        raise InsufficientBudgetError(
            f"no budget account for venture {venture_id} in {currency}"
        )
    account_id, granted, reserved, committed = account

    state = _capital_state(cur, action_request_id)
    if "RESERVE" in state:
        return False  # already reserved -> idempotent, no double reservation

    available = Decimal(granted) - Decimal(reserved) - Decimal(committed)
    if available < amount:
        raise InsufficientBudgetError(
            f"insufficient budget: available {available} < requested {amount}"
        )

    cur.execute(
        "UPDATE budget_account SET reserved_amount = reserved_amount + %s, "
        "updated_at = now() WHERE id = %s",
        (amount, account_id),
    )
    cur.execute(
        """
        INSERT INTO capital_entry
            (venture_id, budget_account_id, action_request_id, entry_type, amount, currency)
        VALUES (%s, %s, %s, 'RESERVE', %s, %s)
        """,
        (venture_id, account_id, action_request_id, amount, currency),
    )
    audit.record_event(
        cur, event_type="budget.reserved", actor=actor, venture_id=venture_id,
        action_id=action_request_id, payload={"amount": str(amount), "currency": currency},
    )
    return True


def _release(cur, action_request_id: str, *, actor: str = "budget") -> bool:
    """Cursor-based release (composes into a larger transaction)."""
    venture_id, _amount, currency = _action_context(cur, action_request_id)
    account = _lock_account(cur, venture_id, currency)
    if account is None:
        return False
    account_id = account[0]

    state = _capital_state(cur, action_request_id)
    if "COMMIT" in state:
        raise IllegalCapitalTransitionError("cannot release a committed reservation")
    if "RELEASE" in state or "RESERVE" not in state:
        return False  # already released or never reserved -> idempotent no-op

    amount = Decimal(state["RESERVE"])
    cur.execute(
        "UPDATE budget_account SET reserved_amount = reserved_amount - %s, "
        "updated_at = now() WHERE id = %s",
        (amount, account_id),
    )
    cur.execute(
        """
        INSERT INTO capital_entry
            (venture_id, budget_account_id, action_request_id, entry_type, amount, currency)
        VALUES (%s, %s, %s, 'RELEASE', %s, %s)
        """,
        (venture_id, account_id, action_request_id, amount, currency),
    )
    audit.record_event(
        cur, event_type="budget.released", actor=actor, venture_id=venture_id,
        action_id=action_request_id, payload={"amount": str(amount), "currency": currency},
    )
    return True


def reserve_budget(conn, action_request_id: str, *, actor: str = "budget") -> bool:
    """Reserve the action's requested amount. Idempotent per action.

    Returns True if a new reservation was made, False if it already existed.
    Raises :class:`InsufficientBudgetError` when available funds are too low.
    """
    with db.transaction(conn) as cur:
        return _reserve(cur, action_request_id, actor=actor)


def release_budget(conn, action_request_id: str, *, actor: str = "budget") -> bool:
    """Release the action's reservation exactly once. Idempotent and safe."""
    with db.transaction(conn) as cur:
        return _release(cur, action_request_id, actor=actor)


def reconcile_completion(cur, action_request_id: str, actual_cost, *, actor: str = "budget") -> None:
    """Reconcile a reservation into actual committed spend (completion path).

    Commits the actual cost and releases any unused reservation, in the caller's
    transaction. Actual cost above the reservation consumes additional available
    budget only if it fits; otherwise raises rather than overspend. Idempotent:
    a second call after a COMMIT exists is a no-op.
    """
    actual = Decimal(actual_cost)
    if actual < 0:
        raise ValueError("actual_cost must be non-negative")
    venture_id, _amt, currency = _action_context(cur, action_request_id)
    account = _lock_account(cur, venture_id, currency)
    if account is None:
        raise IllegalCapitalTransitionError("no budget account to reconcile against")
    account_id, granted, reserved, committed = account

    state = _capital_state(cur, action_request_id)
    if "COMMIT" in state:
        return  # already reconciled -> idempotent
    if "RESERVE" not in state:
        raise IllegalCapitalTransitionError("cannot reconcile without a reservation")
    reserved_amt = Decimal(state["RESERVE"])

    available_excl = Decimal(granted) - Decimal(reserved) - Decimal(committed)
    delta = actual - reserved_amt
    if delta > available_excl:
        raise InsufficientBudgetError(
            f"actual cost {actual} exceeds available budget (delta {delta} > {available_excl})"
        )

    cur.execute(
        "UPDATE budget_account SET reserved_amount = reserved_amount - %s, "
        "committed_amount = committed_amount + %s, updated_at = now() WHERE id = %s",
        (reserved_amt, actual, account_id),
    )
    cur.execute(
        """
        INSERT INTO capital_entry
            (venture_id, budget_account_id, action_request_id, entry_type, amount, currency, reference)
        VALUES (%s, %s, %s, 'COMMIT', %s, %s, 'actual')
        """,
        (venture_id, account_id, action_request_id, actual, currency),
    )
    if reserved_amt > actual:
        cur.execute(
            """
            INSERT INTO capital_entry
                (venture_id, budget_account_id, action_request_id, entry_type, amount, currency, reference)
            VALUES (%s, %s, %s, 'RELEASE', %s, %s, 'unused')
            """,
            (venture_id, account_id, action_request_id, reserved_amt - actual, currency),
        )
    audit.record_event(
        cur, event_type="budget.reconciled", actor=actor, venture_id=venture_id,
        action_id=action_request_id,
        payload={"actual": str(actual), "reserved": str(reserved_amt), "currency": currency},
    )


def commit_budget(conn, action_request_id: str, *, actor: str = "budget") -> bool:
    """Convert the action's reservation into committed spend exactly once."""
    with db.transaction(conn) as cur:
        venture_id, _amount, currency = _action_context(cur, action_request_id)
        account = _lock_account(cur, venture_id, currency)
        if account is None:
            raise IllegalCapitalTransitionError("no budget account to commit against")
        account_id = account[0]

        state = _capital_state(cur, action_request_id)
        if "COMMIT" in state:
            return False  # already committed -> idempotent no-op
        if "RELEASE" in state:
            raise IllegalCapitalTransitionError("cannot commit a released reservation")
        if "RESERVE" not in state:
            raise IllegalCapitalTransitionError("cannot commit without a reservation")

        amount = Decimal(state["RESERVE"])
        cur.execute(
            "UPDATE budget_account SET reserved_amount = reserved_amount - %s, "
            "committed_amount = committed_amount + %s, updated_at = now() WHERE id = %s",
            (amount, amount, account_id),
        )
        cur.execute(
            """
            INSERT INTO capital_entry
                (venture_id, budget_account_id, action_request_id, entry_type, amount, currency)
            VALUES (%s, %s, %s, 'COMMIT', %s, %s)
            """,
            (venture_id, account_id, action_request_id, amount, currency),
        )
        audit.record_event(
            cur,
            event_type="budget.committed",
            actor=actor,
            venture_id=venture_id,
            action_id=action_request_id,
            payload={"amount": str(amount), "currency": currency},
        )
    return True


def get_account(conn, venture_id: str, currency: str):
    """Return ``(id, granted, reserved, committed)`` for a venture account, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, granted_amount, reserved_amount, committed_amount "
            "FROM budget_account WHERE venture_id = %s AND currency = %s",
            (venture_id, currency),
        )
        return cur.fetchone()
