"""Validation precommitment: hypotheses, immutable tests, observed results.

A validation test is a precommitted, immutable definition — success/kill criteria
are fixed before any result exists and cannot be rewritten (anti-hindsight). A
result's PASS/FAIL/INCONCLUSIVE outcome is DERIVED deterministically from the
precommitted structured criteria and the observed measurement; it is never
caller-asserted and never certified by a model. Observed measurement is stored
separately from interpretation. Contradictory results coexist and are never
overwritten. Nothing here spends capital, decides investment, or moves lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from psycopg.types.json import Json

from . import audit, db
from .actions import canonical_payload_hash
from .errors import IdempotencyConflictError, NotFoundError

_COMPARATORS = {"GTE", "LTE", "GT", "LT", "EQ", "IS_TRUE"}
_WTP = {"STATED_INTEREST", "STATED_WILLINGNESS", "SIGNED_COMMITMENT", "ACTUAL_PAYMENT", "NOT_APPLICABLE"}
_MEASUREMENT = {"OUTREACH_RESPONSE", "LANDING_CONVERSION", "ACQUISITION_COST", "ACTIVATION",
                "RETENTION", "USAGE", "OTHER_MEASURED_METRIC"}
_TEST_TYPES = {"INTERVIEW", "LOI", "PILOT", "PREORDER", "LANDING_PAGE", "OUTREACH",
               "PRICING", "USAGE", "BENCHMARK", "OTHER"}


# --------------------------------------------------------------------------
# Hypothesis.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class HypothesisResult:
    hypothesis_id: str
    created: bool


def create_hypothesis(
    conn, venture_id: str, *, opportunity_id: str, statement: str, hypothesis_key: str,
    assumption_id: Optional[str] = None, critical_unknown: Optional[str] = None, actor: str = "aidan",
) -> HypothesisResult:
    if not statement:
        raise ValueError("statement is required")
    if not hypothesis_key:
        raise ValueError("hypothesis_key is required")
    digest = canonical_payload_hash({"statement": statement, "opportunity": str(opportunity_id)})
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT id, statement_hash FROM validation_hypothesis WHERE venture_id = %s AND hypothesis_key = %s",
            (venture_id, hypothesis_key),
        )
        row = cur.fetchone()
        if row is not None:
            if row[1] != digest:
                raise IdempotencyConflictError(f"hypothesis key {hypothesis_key!r} reused with different content")
            return HypothesisResult(row[0], created=False)
        cur.execute(
            "INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, assumption_id, "
            "statement, statement_hash, critical_unknown) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (venture_id, hypothesis_key, opportunity_id, assumption_id, statement, digest, critical_unknown),
        )
        hid = cur.fetchone()[0]
        audit.record_event(cur, event_type="validation.hypothesis_created", actor=actor, venture_id=venture_id,
                           payload={"hypothesis_id": str(hid), "opportunity_id": str(opportunity_id)})
    return HypothesisResult(hid, created=True)


# --------------------------------------------------------------------------
# Test (immutable precommitted definition).
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TestResult:
    test_id: str
    created: bool


def _test_hash(**kw) -> str:
    return canonical_payload_hash({k: (str(v) if v is not None else "") for k, v in kw.items()})


def create_test(
    conn, venture_id: str, *, validation_hypothesis_id: str, test_key: str, test_type: str, method: str,
    success_criterion: str, evidence_required: str, kill_criterion: Optional[str] = None,
    target_segment: Optional[str] = None, max_spend=None, max_duration_days: Optional[int] = None,
    success_metric: Optional[str] = None, success_comparator: Optional[str] = None, success_threshold=None,
    kill_metric: Optional[str] = None, kill_comparator: Optional[str] = None, kill_threshold=None,
    actor: str = "aidan",
) -> TestResult:
    if test_type not in _TEST_TYPES:
        raise ValueError(f"test_type must be one of {sorted(_TEST_TYPES)}")
    if not success_criterion:
        raise ValueError("success_criterion is required (precommitted)")
    if not evidence_required:
        raise ValueError("evidence_required is required (precommitted)")
    for name, comp in (("success_comparator", success_comparator), ("kill_comparator", kill_comparator)):
        if comp is not None and comp not in _COMPARATORS:
            raise ValueError(f"{name} must be one of {sorted(_COMPARATORS)}")

    fields = dict(
        validation_hypothesis_id=validation_hypothesis_id, test_type=test_type, method=method,
        target_segment=target_segment, success_criterion=success_criterion, kill_criterion=kill_criterion,
        evidence_required=evidence_required, max_spend=max_spend, max_duration_days=max_duration_days,
        success_metric=success_metric, success_comparator=success_comparator, success_threshold=success_threshold,
        kill_metric=kill_metric, kill_comparator=kill_comparator, kill_threshold=kill_threshold,
    )
    digest = _test_hash(**fields)  # computed once from the caller's input (idempotency anchor)
    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT id, definition_hash FROM validation_test WHERE venture_id = %s AND test_key = %s",
            (venture_id, test_key),
        )
        row = cur.fetchone()
        if row is not None:
            if row[1] != digest:
                raise IdempotencyConflictError(f"test key {test_key!r} reused with a different definition")
            return TestResult(row[0], created=False)
        cur.execute(
            """
            INSERT INTO validation_test
                (venture_id, test_key, validation_hypothesis_id, test_type, method, target_segment,
                 success_criterion, kill_criterion, evidence_required, max_spend, max_duration_days,
                 success_metric, success_comparator, success_threshold, kill_metric, kill_comparator, kill_threshold,
                 definition_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, test_key, validation_hypothesis_id, test_type, method, target_segment,
             success_criterion, kill_criterion, evidence_required, max_spend, max_duration_days,
             success_metric, success_comparator, success_threshold, kill_metric, kill_comparator, kill_threshold,
             digest),
        )
        tid = cur.fetchone()[0]
        audit.record_event(cur, event_type="validation.test_defined", actor=actor, venture_id=venture_id,
                           payload={"test_id": str(tid), "hypothesis_id": str(validation_hypothesis_id)})
    return TestResult(tid, created=True)


def link_action_request(conn, *, test_id: str, action_request_id: str, actor: str = "aidan") -> bool:
    """Set-once link from a test to a same-venture ActionRequest. No spend/approval/execution."""
    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id, action_request_id FROM validation_test WHERE id = %s FOR UPDATE", (test_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"validation test {test_id} does not exist")
        venture_id, existing = row
        if existing is not None:
            if str(existing) == str(action_request_id):
                return False
            raise IdempotencyConflictError("validation test already linked to a different ActionRequest")
        # Composite FK enforces the ActionRequest belongs to the same venture.
        cur.execute("UPDATE validation_test SET action_request_id = %s WHERE id = %s", (action_request_id, test_id))
        audit.record_event(cur, event_type="validation.test_action_linked", actor=actor, venture_id=venture_id,
                           payload={"test_id": str(test_id), "action_request_id": str(action_request_id)})
    return True


# --------------------------------------------------------------------------
# Result (outcome is DERIVED deterministically, never caller-asserted).
# --------------------------------------------------------------------------
def _compare(value, comparator: Optional[str], threshold) -> bool:
    if comparator is None:
        return False
    if comparator == "IS_TRUE":
        return value is True
    if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    v, t = float(value), float(threshold)
    return {"GTE": v >= t, "LTE": v <= t, "GT": v > t, "LT": v < t, "EQ": v == t}[comparator]


def evaluate_outcome(test_row: dict, observed_value: dict) -> str:
    """Kill criterion outranks success; absent/subjective criteria -> INCONCLUSIVE (never auto-PASS)."""
    if test_row.get("kill_metric") is not None and _compare(
        observed_value.get(test_row["kill_metric"]), test_row.get("kill_comparator"), test_row.get("kill_threshold")
    ):
        return "FAIL"
    if test_row.get("success_metric") is not None and _compare(
        observed_value.get(test_row["success_metric"]), test_row.get("success_comparator"), test_row.get("success_threshold")
    ):
        return "PASS"
    return "INCONCLUSIVE"


@dataclass(frozen=True)
class ValidationResult:
    result_id: str
    outcome: str
    created: bool


def record_result(
    conn, *, validation_test_id: str, result_key: str, observed_value: Optional[dict] = None,
    interpretation: Optional[str] = None, wtp_modality: Optional[str] = None,
    measurement_kind: Optional[str] = None, observation_id: Optional[str] = None,
    external_result_ref: Optional[str] = None, actor: str = "aidan",
) -> ValidationResult:
    if wtp_modality is not None and wtp_modality not in _WTP:
        raise ValueError(f"wtp_modality must be one of {sorted(_WTP)}")
    if measurement_kind is not None and measurement_kind not in _MEASUREMENT:
        raise ValueError(f"measurement_kind must be one of {sorted(_MEASUREMENT)}")
    observed_value = observed_value or {}
    observed_hash = canonical_payload_hash(observed_value)

    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT venture_id, success_metric, success_comparator, success_threshold, "
            "kill_metric, kill_comparator, kill_threshold FROM validation_test WHERE id = %s",
            (validation_test_id,),
        )
        trow = cur.fetchone()
        if trow is None:
            raise NotFoundError(f"validation test {validation_test_id} does not exist")
        venture_id = trow[0]
        test_row = {
            "success_metric": trow[1], "success_comparator": trow[2], "success_threshold": trow[3],
            "kill_metric": trow[4], "kill_comparator": trow[5], "kill_threshold": trow[6],
        }
        outcome = evaluate_outcome(test_row, observed_value)  # DERIVED, not supplied

        cur.execute("SELECT id, observed_hash FROM validation_result WHERE venture_id = %s AND result_key = %s",
                    (venture_id, result_key))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != observed_hash:
                raise IdempotencyConflictError(f"result key {result_key!r} reused with a different observation")
            return ValidationResult(existing[0], outcome, created=False)

        cur.execute(
            """
            INSERT INTO validation_result
                (venture_id, validation_test_id, result_key, observed_value, observed_hash, interpretation,
                 outcome, wtp_modality, measurement_kind, observation_id, external_result_ref)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, validation_test_id, result_key, Json(observed_value), observed_hash, interpretation,
             outcome, wtp_modality, measurement_kind, observation_id, external_result_ref),
        )
        rid = cur.fetchone()[0]
        audit.record_event(cur, event_type="validation.result_recorded", actor=actor, venture_id=venture_id,
                           payload={"result_id": str(rid), "validation_test_id": str(validation_test_id), "outcome": outcome})
    return ValidationResult(rid, outcome, created=True)


def get_result(conn, result_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, validation_test_id, observed_value, interpretation, outcome, "
            "wtp_modality, measurement_kind, observation_id, external_result_ref FROM validation_result WHERE id = %s",
            (result_id,),
        )
        return cur.fetchone()


def provenance(conn, result_id: str) -> dict:
    """Structured traversal: result -> test -> hypothesis -> opportunity (+ assumption/observation/source)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.outcome, r.observed_value, r.interpretation, r.wtp_modality, r.measurement_kind,
                   r.observation_id,
                   t.id, t.success_criterion, t.kill_criterion, t.evidence_required,
                   h.id, h.statement, h.critical_unknown, h.opportunity_id, h.assumption_id
            FROM validation_result r
            JOIN validation_test t ON t.id = r.validation_test_id
            JOIN validation_hypothesis h ON h.id = t.validation_hypothesis_id
            WHERE r.id = %s
            """,
            (result_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"validation result {result_id} does not exist")
        source = None
        if row[6] is not None:
            cur.execute(
                "SELECT o.source_evidence_id, sr.locator FROM observation o "
                "JOIN source_receipt sr ON sr.evidence_record_id = o.source_evidence_id WHERE o.evidence_record_id = %s",
                (row[6],),
            )
            source = cur.fetchone()
    return {
        "result_id": row[0], "outcome": row[1], "observed_value": row[2], "interpretation": row[3],
        "wtp_modality": row[4], "measurement_kind": row[5], "observation_id": row[6],
        "test": {"test_id": row[7], "success_criterion": row[8], "kill_criterion": row[9], "evidence_required": row[10]},
        "hypothesis": {"hypothesis_id": row[11], "statement": row[12], "critical_unknown": row[13],
                       "opportunity_id": row[14], "assumption_id": row[15]},
        "source": {"source_evidence_id": source[0], "locator": source[1]} if source else None,
    }
