"""Deterministic Policy Engine (Slice 3).

``evaluate`` is a pure function with a fixed precedence. The only way to persist
a decision is :func:`evaluate_and_persist`, which computes inputs from canonical
state and the evaluation itself — callers can never supply the outcome. There is
no policy DSL and no LLM.

Precedence (highest first):
  1. GLOBAL kill switch active     -> DENY
  2. VENTURE kill switch active    -> DENY
  3. insufficient available budget -> DENY
  4. autonomy level insufficient   -> REQUIRE_APPROVAL
  5. amount above approval threshold -> REQUIRE_APPROVAL
  6. otherwise                     -> ALLOW
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from . import audit, db
from .actions import canonical_payload_hash
from .errors import NotFoundError
from .killswitch import effective_state
from .models import PolicyOutcome

RULE_ID = "gate1.policy"
RULE_VERSION = "1"
DEFAULT_APPROVAL_THRESHOLD = Decimal("1000.0000")


@dataclass(frozen=True)
class PolicyInput:
    action_type: str
    venture_autonomy: int
    required_autonomy: int
    global_kill: bool
    venture_kill: bool
    available_budget: Decimal
    requested_amount: Decimal
    currency: str
    approval_threshold: Decimal


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    reason: str
    inputs_hash: str


def _inputs_dict(inp: PolicyInput) -> dict:
    return {
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "action_type": inp.action_type,
        "venture_autonomy": inp.venture_autonomy,
        "required_autonomy": inp.required_autonomy,
        "global_kill": inp.global_kill,
        "venture_kill": inp.venture_kill,
        "available_budget": str(inp.available_budget),
        "requested_amount": str(inp.requested_amount),
        "currency": inp.currency,
        "approval_threshold": str(inp.approval_threshold),
    }


def evaluate(inp: PolicyInput) -> PolicyResult:
    """Pure, deterministic policy evaluation. Same inputs -> same result."""
    inputs_hash = canonical_payload_hash(_inputs_dict(inp))

    if inp.global_kill:
        return PolicyResult(PolicyOutcome.DENY.value, "KILL_SWITCH_GLOBAL", inputs_hash)
    if inp.venture_kill:
        return PolicyResult(PolicyOutcome.DENY.value, "KILL_SWITCH_VENTURE", inputs_hash)
    if inp.available_budget < inp.requested_amount:
        return PolicyResult(PolicyOutcome.DENY.value, "INSUFFICIENT_BUDGET", inputs_hash)
    if inp.required_autonomy > inp.venture_autonomy:
        return PolicyResult(
            PolicyOutcome.REQUIRE_APPROVAL.value, "AUTONOMY_INSUFFICIENT", inputs_hash
        )
    if inp.requested_amount > inp.approval_threshold:
        return PolicyResult(
            PolicyOutcome.REQUIRE_APPROVAL.value, "ABOVE_APPROVAL_THRESHOLD", inputs_hash
        )
    return PolicyResult(PolicyOutcome.ALLOW.value, "ALLOWED", inputs_hash)


def _load_policy_input(cur, action_request_id: str, approval_threshold: Decimal) -> Tuple[PolicyInput, str]:
    cur.execute(
        """
        SELECT ar.venture_id, ar.action_type, ar.required_autonomy,
               ar.requested_amount, ar.requested_currency, v.autonomy_level
        FROM action_request ar
        JOIN venture v ON v.id = ar.venture_id
        WHERE ar.id = %s
        """,
        (action_request_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"action_request {action_request_id} does not exist")
    venture_id, action_type, required_autonomy, requested_amount, currency, autonomy = row

    ks = effective_state(cur, venture_id)

    cur.execute(
        """
        SELECT coalesce(granted_amount - reserved_amount - committed_amount, 0)
        FROM budget_account
        WHERE venture_id = %s AND currency = %s
        """,
        (venture_id, currency),
    )
    brow = cur.fetchone()
    available = Decimal(brow[0] if brow is not None else 0)

    # This action's OWN active reservation is available to itself: re-evaluating an
    # already-reserved action (a retry, or a completion-time recheck) must not
    # require a second copy of the same capital. Add its held reservation back to
    # available (an entry that is RESERVEd but neither RELEASEd nor COMMITted).
    cur.execute(
        "SELECT entry_type FROM capital_entry WHERE action_request_id = %s AND currency = %s",
        (action_request_id, currency),
    )
    own_entries = {r[0] for r in cur.fetchall()}
    if "RESERVE" in own_entries and "RELEASE" not in own_entries and "COMMIT" not in own_entries:
        available += Decimal(requested_amount)

    return (
        PolicyInput(
            action_type=action_type,
            venture_autonomy=autonomy,
            required_autonomy=required_autonomy,
            global_kill=ks["global"],
            venture_kill=ks["venture"],
            available_budget=Decimal(available),
            requested_amount=Decimal(requested_amount),
            currency=currency,
            approval_threshold=approval_threshold,
        ),
        venture_id,
    )


def current_evaluation(
    cur, action_request_id: str, approval_threshold: Decimal = DEFAULT_APPROVAL_THRESHOLD
) -> Tuple[PolicyResult, str, PolicyInput]:
    """Evaluate current policy from canonical state WITHOUT persisting.

    Used at execution-authorization time to recheck kill switch, budget and
    autonomy against the live state. Returns ``(result, venture_id, inputs)``.
    Identical inputs always yield the same ``inputs_hash`` — that hash is the
    authorization-state identity an approval is bound to.
    """
    inp, venture_id = _load_policy_input(cur, action_request_id, approval_threshold)
    return evaluate(inp), venture_id, inp


def persist_decision(cur, action_request_id: str, venture_id: str, result: PolicyResult, inp: PolicyInput) -> str:
    """Persist an immutable policy decision + audit within the caller's transaction."""
    from psycopg.types.json import Json

    cur.execute(
        """
        INSERT INTO policy_decision
            (action_request_id, decision, rule_id, rule_version, inputs_hash, inputs, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            action_request_id,
            result.decision,
            RULE_ID,
            RULE_VERSION,
            result.inputs_hash,
            Json(_inputs_dict(inp)),
            result.reason,
        ),
    )
    decision_id = cur.fetchone()[0]
    audit.record_event(
        cur,
        event_type="policy.evaluated",
        actor="policy_engine",
        venture_id=venture_id,
        action_id=action_request_id,
        payload={"decision": result.decision, "reason": result.reason},
    )
    return decision_id


def evaluate_and_persist(
    conn,
    action_request_id: str,
    *,
    approval_threshold: Decimal = DEFAULT_APPROVAL_THRESHOLD,
    actor: str = "policy_engine",
) -> Tuple[PolicyResult, str]:
    """Evaluate policy from canonical state, persist an immutable decision, audit.

    Returns ``(PolicyResult, policy_decision_id)``. The decision is computed
    here; there is deliberately no API to persist a caller-supplied outcome.
    This is a governance decision only: it never mutates lifecycle, investment
    decisions or ActionRequest status.
    """
    with db.transaction(conn) as cur:
        result, venture_id, inp = current_evaluation(cur, action_request_id, approval_threshold)
        decision_id = persist_decision(cur, action_request_id, venture_id, result, inp)
    return result, decision_id
