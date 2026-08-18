"""Governed conversion of a next-action recommendation into a canonical
investment decision (Gate 3, Slice 3).

A :mod:`nextaction` recommendation is non-authoritative reasoning. This module is
the ONLY governed path that turns one into a canonical
``investment_decision_record`` and, when a bounded consequential amount is
honestly supplied, an intake ``action_request`` for the Gate 1 chain to govern.

The separations Gate 3 must preserve are enforced here, not assumed:

  Recommendation  !=  Investment Decision  !=  ActionRequest
                  !=  Policy Authorization  !=  Execution  !=  Lifecycle State

Concretely, ``commit_recommendation`` only ever writes an investment decision and
(optionally) a PENDING ActionRequest. It never evaluates policy, approves,
reserves/commits capital, executes, sets ActionRequest SUCCESS, writes a Proof
Receipt, or moves venture lifecycle — those remain the Gate 1 chain's authority
(ActionRequest -> Policy -> Approval -> Execution -> Proof Receipt).

Doctrine enforced:
- STALENESS: a recommendation encodes an exact input basis. Before conversion the
  basis is recomputed against current canonical state; if it changed materially
  the old recommendation is rejected as stale (never mutated) and a fresh one
  must be obtained. So a BUILD recommendation followed by a decisive validation
  FAIL cannot be committed as BUILD.
- COMPATIBILITY: only the standard 1:1 mappings are permitted
  (VALIDATE->VALIDATE, BUILD->BUILD, HOLD->HOLD, KILL->KILL). RESEARCH_MORE maps
  to NO investment decision. Incompatible targets (e.g. KILL->BUILD, BUILD->SCALE)
  are refused.
- BUILD RE-GATE: BUILD is never trusted because it was recommended; current
  canonical state is independently re-verified before a BUILD decision.
- VALIDATE BOUNDARY: a consequential validation amount may not exceed the selected
  validation test's precommitted ``max_spend``.
- ATOMICITY: the decision and any resulting ActionRequest are written in one
  transaction, so a decision never claims an action that failed to persist and a
  refused decision leaves no orphan ActionRequest.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Union

from psycopg.types.json import Json

from . import audit, db, nextaction
from .actions import canonical_payload_hash
from .errors import (
    BuildGateNotSatisfiedError,
    ConsequentialSpendError,
    NotFoundError,
    RecommendationNotConvertibleError,
    StaleRecommendationError,
)
from .models import InvestmentDecision

# The only permitted recommendation -> investment-decision mappings. RESEARCH_MORE
# is intentionally absent: it is a next-action recommendation, not a decision.
_RECOMMENDATION_TO_DECISION = {
    "VALIDATE": "VALIDATE",
    "BUILD": "BUILD",
    "HOLD": "HOLD",
    "KILL": "KILL",
}

# Default ActionRequest action_type for a consequential request, per decision.
_DEFAULT_ACTION_TYPE = {"VALIDATE": "validation_spend", "BUILD": "build"}

# Validation measurement kinds that represent acquisition/channel evidence.
_ACQUISITION_KINDS = ("OUTREACH_RESPONSE", "LANDING_CONVERSION", "ACQUISITION_COST")


@dataclass(frozen=True)
class CommitmentResult:
    decision_id: str
    decision: str
    resulting_action_id: Optional[str]
    created: bool


def _as_decision_value(decision: Union[str, InvestmentDecision]) -> str:
    return decision.value if isinstance(decision, InvestmentDecision) else str(decision)


def _load_recommendation(cur, recommendation_id: str):
    cur.execute(
        "SELECT id, venture_id, opportunity_id, action_type, input_hash, selected_validation_test_id "
        "FROM next_action_recommendation WHERE id = %s",
        (recommendation_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"recommendation {recommendation_id} does not exist")
    return row


def _assert_not_stale(cur, venture_id, opportunity_id, action_type, stored_hash):
    """Recompute the recommendation's basis now; reject if it materially changed."""
    basis = nextaction.current_basis(cur, venture_id, opportunity_id)
    if basis["input_hash"] != stored_hash:
        raise StaleRecommendationError(
            f"recommendation is stale: current basis ({basis['action']}) no longer matches the "
            f"basis it was produced against ({action_type}); obtain a fresh recommendation"
        )


def _build_gate(cur, venture_id, opportunity_id) -> None:
    """Independently re-verify current canonical state for a BUILD decision.

    Raises :class:`BuildGateNotSatisfiedError` listing every unmet condition. No
    single universal rule (e.g. a WTP modality or numeric threshold) is imposed:
    PASS is always the validation test's OWN precommitted criterion.
    """
    failures: list[str] = []

    cur.execute(
        "SELECT status FROM opportunity WHERE id = %s AND venture_id = %s",
        (opportunity_id, venture_id),
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"opportunity {opportunity_id} does not exist for venture {venture_id}")
    if row[0] != "CANDIDATE":
        failures.append(f"opportunity status is {row[0]}, not CANDIDATE")

    # Genuine positive provenance: at least one linked Claim whose derived state is
    # SUPPORTED (Claim -> SUPPORTS -> Observation -> Source Receipt, no contradiction).
    cur.execute("SELECT claim_id FROM opportunity_claim WHERE opportunity_id = %s", (opportunity_id,))
    supported = False
    for (claim_id,) in cur.fetchall():
        cur.execute(
            "SELECT count(*) FILTER (WHERE stance = 'SUPPORTS'), "
            "       count(*) FILTER (WHERE stance = 'CONTRADICTS') "
            "FROM claim_evidence WHERE claim_id = %s",
            (claim_id,),
        )
        supports, contradicts = cur.fetchone()
        if supports > 0 and contradicts == 0:
            supported = True
            break
    if not supported:
        failures.append("no linked Claim with positive provenance (Claim -> SUPPORTS -> Observation -> Source Receipt)")

    # Complete adversarial Kill Case.
    cur.execute("SELECT id FROM kill_case WHERE opportunity_id = %s", (opportunity_id,))
    kc = cur.fetchone()
    if kc is None:
        failures.append("no adversarial Kill Case")
    else:
        from .research import killcase

        cur.execute("SELECT dimension FROM kill_case_dimension WHERE kill_case_id = %s", (kc[0],))
        present = {r[0] for r in cur.fetchall()}
        missing = set(killcase.REQUIRED_DIMENSIONS) - present
        if missing:
            failures.append(f"Kill Case missing dimensions: {sorted(missing)}")

    # Per-assumption outcomes: no unresolved CRITICAL assumption; no ignored
    # PASS/INCONCLUSIVE contradiction.
    cur.execute(
        "SELECT a.id, a.importance FROM opportunity_assumption oa "
        "JOIN assumption a ON a.id = oa.assumption_id WHERE oa.opportunity_id = %s ORDER BY a.id",
        (opportunity_id,),
    )
    for aid, importance in cur.fetchall():
        cur.execute(
            "SELECT r.outcome FROM validation_result r "
            "JOIN validation_test t ON t.id = r.validation_test_id "
            "JOIN validation_hypothesis h ON h.id = t.validation_hypothesis_id "
            "WHERE h.assumption_id = %s AND h.venture_id = %s",
            (aid, venture_id),
        )
        outcomes = [o for (o,) in cur.fetchall()]
        has_pass = "PASS" in outcomes
        has_fail = "FAIL" in outcomes
        has_inconclusive = "INCONCLUSIVE" in outcomes
        if importance == "CRITICAL" and not (has_pass and not has_inconclusive and not has_fail):
            failures.append(f"critical assumption {aid} is unresolved")
        if has_pass and has_inconclusive:
            failures.append(f"assumption {aid} has an unresolved PASS/INCONCLUSIVE contradiction")

    # Opportunity-scoped validation evidence: no decisive kill triggered; a WTP-context
    # PASS and an acquisition/channel-context PASS both exist.
    cur.execute(
        "SELECT r.outcome, r.wtp_modality, r.measurement_kind FROM validation_result r "
        "JOIN validation_test t ON t.id = r.validation_test_id "
        "JOIN validation_hypothesis h ON h.id = t.validation_hypothesis_id "
        "WHERE h.opportunity_id = %s AND h.venture_id = %s",
        (opportunity_id, venture_id),
    )
    rows = cur.fetchall()
    if any(o == "FAIL" for o, _w, _m in rows):
        failures.append("a decisive kill criterion is currently triggered (a FAIL result exists)")
    if not any(o == "PASS" and w is not None and w != "NOT_APPLICABLE" for o, w, _m in rows):
        failures.append("no contextual WTP validation PASS against a precommitted criterion")
    if not any(o == "PASS" and m in _ACQUISITION_KINDS for o, _w, m in rows):
        failures.append("no contextual acquisition/channel validation PASS against a precommitted criterion")

    if failures:
        raise BuildGateNotSatisfiedError("BUILD refused by canonical re-gating: " + "; ".join(failures))


def _validate_spend_bound(cur, venture_id, selected_test_id, amount: Decimal) -> None:
    """A consequential VALIDATE amount must be bounded by the test's max_spend."""
    if selected_test_id is None:
        raise ConsequentialSpendError(
            "VALIDATE recommendation selected no validation test; cannot authorise consequential spend"
        )
    cur.execute(
        "SELECT max_spend FROM validation_test WHERE id = %s AND venture_id = %s",
        (selected_test_id, venture_id),
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"validation test {selected_test_id} does not exist for venture {venture_id}")
    max_spend = row[0]
    if max_spend is None:
        raise ConsequentialSpendError(
            "selected validation test has no precommitted max_spend; consequential spend is not permitted"
        )
    if amount > max_spend:
        raise ConsequentialSpendError(
            f"requested amount {amount} exceeds validation_test.max_spend {max_spend}"
        )


def _intake_action(
    cur, *, venture_id, recommendation_id, action_type, payload, required_autonomy,
    amount: Decimal, currency: str, actor: str,
) -> str:
    """Idempotently intake a PENDING ActionRequest (no policy/approval/execution)."""
    payload_hash = canonical_payload_hash(payload)
    idempotency_key = f"commit:{recommendation_id}"
    cur.execute(
        "INSERT INTO action_request "
        "(venture_id, action_type, actor, payload, payload_hash, idempotency_key, "
        " required_autonomy, requested_amount, requested_currency) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (venture_id, idempotency_key) DO NOTHING RETURNING id",
        (venture_id, action_type, actor, Json(payload), payload_hash, idempotency_key,
         required_autonomy, amount, currency),
    )
    row = cur.fetchone()
    if row is not None:
        audit.record_event(
            cur, event_type="action_request.created", actor=actor, venture_id=venture_id,
            action_id=row[0], payload={"action_type": action_type, "payload_hash": payload_hash},
        )
        return row[0]
    cur.execute(
        "SELECT id FROM action_request WHERE venture_id = %s AND idempotency_key = %s",
        (venture_id, idempotency_key),
    )
    return cur.fetchone()[0]


def commit_recommendation(
    conn,
    recommendation_id: str,
    *,
    decision: Optional[Union[str, InvestmentDecision]] = None,
    requested_amount: Optional[Any] = None,
    requested_currency: str = "USD",
    required_autonomy: int = 0,
    action_type: Optional[str] = None,
    actor: str = "aidan",
) -> CommitmentResult:
    """Convert a current recommendation into a governed investment decision.

    ``decision`` may be omitted (derived from the recommendation) or given
    explicitly; an incompatible explicit target is refused. ``requested_amount``,
    when > 0, creates a PENDING Gate 1 ActionRequest for the consequential
    request (VALIDATE spend bounded by the test's max_spend; BUILD only when a
    bounded amount is honestly supplied). Idempotent per recommendation.
    """
    with db.transaction(conn) as cur:
        rec_id, venture_id, opportunity_id, action_type_rec, input_hash, selected_test = _load_recommendation(
            cur, recommendation_id
        )

        # Idempotent: one governed decision per recommendation.
        cur.execute(
            "SELECT id, decision, resulting_action_id FROM investment_decision_record "
            "WHERE source_recommendation_id = %s",
            (rec_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            return CommitmentResult(existing[0], existing[1], existing[2], created=False)

        # RESEARCH_MORE (and any non-standard action) maps to no investment decision.
        target = _RECOMMENDATION_TO_DECISION.get(action_type_rec)
        if target is None:
            raise RecommendationNotConvertibleError(
                f"recommendation action {action_type_rec} does not map to an investment decision"
            )
        if decision is not None and _as_decision_value(decision) != target:
            raise RecommendationNotConvertibleError(
                f"incompatible mapping: {action_type_rec} recommendation cannot become a "
                f"{_as_decision_value(decision)} decision (only {target} is permitted)"
            )
        decision_value = target

        # A recommendation may only be committed while it still reflects current state.
        _assert_not_stale(cur, venture_id, opportunity_id, action_type_rec, input_hash)

        # Consequential amount handling (defaults to no spend).
        amount = Decimal(str(requested_amount)) if requested_amount is not None else Decimal(0)
        consequential = amount > 0

        if consequential and decision_value in ("HOLD", "KILL"):
            raise ConsequentialSpendError(
                f"a {decision_value} decision carries no consequential spend"
            )

        # BUILD is re-gated against current canonical state regardless of amount.
        if decision_value == "BUILD":
            _build_gate(cur, venture_id, opportunity_id)

        # VALIDATE consequential spend is bounded by the selected test's precommitment.
        if decision_value == "VALIDATE" and consequential:
            _validate_spend_bound(cur, venture_id, selected_test, amount)

        # Optionally intake the consequential ActionRequest (VALIDATE spend or a
        # BUILD with an honestly supplied bounded amount). No policy/approval/exec.
        resulting_action_id = None
        if consequential and decision_value in ("VALIDATE", "BUILD"):
            payload = {
                "recommendation_id": str(rec_id),
                "opportunity_id": str(opportunity_id),
                "decision": decision_value,
            }
            if decision_value == "VALIDATE":
                payload["validation_test_id"] = str(selected_test)
            resulting_action_id = _intake_action(
                cur, venture_id=venture_id, recommendation_id=rec_id,
                action_type=action_type or _DEFAULT_ACTION_TYPE[decision_value],
                payload=payload, required_autonomy=required_autonomy,
                amount=amount, currency=requested_currency, actor=actor,
            )

        # Record the canonical decision, linked to its recommendation basis and
        # (if any) resulting action — atomically with the action above.
        cur.execute(
            "INSERT INTO investment_decision_record "
            "(venture_id, decision, rationale_ref, resulting_action_id, source_recommendation_id) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (venture_id, decision_value, f"next_action_recommendation:{rec_id}",
             resulting_action_id, rec_id),
        )
        decision_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="investment_decision.committed", actor=actor, venture_id=venture_id,
            action_id=resulting_action_id,
            payload={
                "decision": decision_value,
                "source_recommendation_id": str(rec_id),
                "resulting_action_id": str(resulting_action_id) if resulting_action_id else None,
            },
        )
    return CommitmentResult(decision_id, decision_value, resulting_action_id, created=True)
