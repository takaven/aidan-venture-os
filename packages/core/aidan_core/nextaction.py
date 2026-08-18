"""Highest-value next-action allocator (Gate 3, Slice 2).

Selects the next action for an Opportunity from current research/validation state,
preferring to resolve important uncertainty cheaply before committing. It is
REASONING only: a recommendation never records an investment decision, spends,
executes, approves, or moves lifecycle. Selection is deterministic and
categorical (reason codes + provenance) — no expected-value/information-gain
decimals, no composite score. Recommendations are append-only; later evidence
produces a new recommendation and never rewrites a prior one.

Doctrine encoded:
- a precommitted kill criterion (a FAIL result) outranks generated optimism -> KILL;
- an unresolved CRITICAL/HIGH assumption with a discriminating test -> VALIDATE;
  without a credible test -> RESEARCH_MORE;
- contradictory-without-decisive-kill (PASS + INCONCLUSIVE) -> VALIDATE or HOLD;
- BUILD is only a recommendation, gated structurally, never authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import audit, db
from .actions import canonical_payload_hash
from .errors import IdempotencyConflictError, NotFoundError

_ACTIONS = {"RESEARCH_MORE", "VALIDATE", "BUILD", "HOLD", "KILL"}


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    action_type: str
    reason_code: str
    created: bool


def _gather(cur, venture_id, opportunity_id):
    """Return per-assumption validation state for the opportunity's assumptions."""
    cur.execute(
        "SELECT a.id, a.importance FROM opportunity_assumption oa JOIN assumption a ON a.id = oa.assumption_id "
        "WHERE oa.opportunity_id = %s ORDER BY a.id",
        (opportunity_id,),
    )
    assumptions = cur.fetchall()
    state = {}
    all_result_ids = []
    for aid, importance in assumptions:
        cur.execute(
            """
            SELECT r.id, r.outcome
            FROM validation_result r
            JOIN validation_test t ON t.id = r.validation_test_id
            JOIN validation_hypothesis h ON h.id = t.validation_hypothesis_id
            WHERE h.assumption_id = %s AND h.venture_id = %s
            ORDER BY r.id
            """,
            (aid, venture_id),
        )
        results = cur.fetchall()
        outcomes = [o for _rid, o in results]
        all_result_ids.extend(rid for rid, _o in results)
        state[aid] = {
            "importance": importance,
            "has_pass": "PASS" in outcomes,
            "has_fail": "FAIL" in outcomes,
            "has_inconclusive": "INCONCLUSIVE" in outcomes,
            "result_ids": [rid for rid, _o in results],
        }
    return assumptions, state, all_result_ids


def _cheapest_discriminating_test(cur, venture_id, assumption_id):
    """A test that can deterministically discriminate (structured success criterion),
    cheapest by real max_spend then max_duration then stable id. No synthetic score."""
    cur.execute(
        """
        SELECT t.id
        FROM validation_test t JOIN validation_hypothesis h ON h.id = t.validation_hypothesis_id
        WHERE h.assumption_id = %s AND h.venture_id = %s AND t.success_metric IS NOT NULL
        ORDER BY coalesce(t.max_spend, 'infinity'::numeric) ASC,
                 coalesce(t.max_duration_days, 2147483647) ASC, t.id ASC
        LIMIT 1
        """,
        (assumption_id, venture_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


_IMPORTANCE_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}


def _decide(cur, venture_id, opportunity_id):
    assumptions, state, all_result_ids = _gather(cur, venture_id, opportunity_id)
    considered_assumptions = [aid for aid, _imp in assumptions]
    selected_test = None
    considered_tests = []

    # 1. Decisive precommitted kill criterion (a FAIL) outranks everything.
    for aid, s in state.items():
        if s["has_fail"]:
            return ("KILL", "KILL_CRITERION_TRIGGERED", None, considered_assumptions, considered_tests, all_result_ids)

    # 2. Unresolved CRITICAL/HIGH assumption (resolved == has PASS and no INCONCLUSIVE).
    unresolved = [
        (aid, s) for aid, s in state.items()
        if s["importance"] in ("CRITICAL", "HIGH") and not (s["has_pass"] and not s["has_inconclusive"])
    ]
    if unresolved:
        unresolved.sort(key=lambda x: _IMPORTANCE_RANK[x[1]["importance"]], reverse=True)
        aid = unresolved[0][0]
        test = _cheapest_discriminating_test(cur, venture_id, aid)
        if test is not None:
            considered_tests.append(test)
            return ("VALIDATE", "CRITICAL_ASSUMPTION_UNRESOLVED", test, considered_assumptions, considered_tests, all_result_ids)
        return ("RESEARCH_MORE", "INSUFFICIENT_EVIDENCE", None, considered_assumptions, considered_tests, all_result_ids)

    # 3. Material contradiction without decisive kill (PASS + INCONCLUSIVE) on any assumption.
    for aid, s in state.items():
        if s["has_pass"] and s["has_inconclusive"]:
            test = _cheapest_discriminating_test(cur, venture_id, aid)
            if test is not None:
                considered_tests.append(test)
                return ("VALIDATE", "VALIDATION_CONTRADICTORY", test, considered_assumptions, considered_tests, all_result_ids)
            return ("HOLD", "VALIDATION_CONTRADICTORY", None, considered_assumptions, considered_tests, all_result_ids)

    # 4. BUILD consideration (structurally complete + positive evidence + all key assumptions resolved).
    cur.execute("SELECT status FROM opportunity WHERE id = %s", (opportunity_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"opportunity {opportunity_id} does not exist")
    status = row[0]
    any_pass = any(s["has_pass"] for s in state.values())
    key_resolved = all(
        (s["has_pass"] and not s["has_inconclusive"] and not s["has_fail"])
        for aid, s in state.items() if s["importance"] in ("CRITICAL", "HIGH")
    )
    if status == "CANDIDATE" and any_pass and key_resolved and state:
        return ("BUILD", "BUILD_CONSIDERATION_READY", None, considered_assumptions, considered_tests, all_result_ids)

    # 5. Otherwise no high-value action now.
    return ("HOLD", "NO_HIGH_VALUE_ACTION_NOW", None, considered_assumptions, considered_tests, all_result_ids)


def recommend(
    conn, venture_id: str, opportunity_id: str, *, recommendation_key: str, actor: str = "aidan",
) -> Recommendation:
    """Select and persist a next-action recommendation. Idempotent per key."""
    if not recommendation_key:
        raise ValueError("recommendation_key is required")
    with db.transaction(conn) as cur:
        action, reason, selected_test, considered_assumptions, considered_tests, considered_results = _decide(
            cur, venture_id, opportunity_id
        )
        input_hash = canonical_payload_hash({
            "opportunity": str(opportunity_id),
            "assumptions": sorted(str(a) for a in considered_assumptions),
            "results": sorted(str(r) for r in considered_results),
            "action": action, "reason": reason,
        })

        cur.execute(
            "SELECT id, input_hash FROM next_action_recommendation WHERE venture_id = %s AND recommendation_key = %s",
            (venture_id, recommendation_key),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != input_hash:
                raise IdempotencyConflictError(
                    f"recommendation key {recommendation_key!r} reused with a changed input state"
                )
            cur.execute("SELECT action_type, dominant_reason_code FROM next_action_recommendation WHERE id = %s", (existing[0],))
            a, rc = cur.fetchone()
            return Recommendation(existing[0], a, rc, created=False)

        cur.execute(
            "INSERT INTO next_action_recommendation "
            "(venture_id, recommendation_key, opportunity_id, action_type, dominant_reason_code, rationale, "
            "input_hash, selected_validation_test_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (venture_id, recommendation_key, opportunity_id, action, reason,
             f"{action} because {reason}", input_hash, selected_test),
        )
        rec_id = cur.fetchone()[0]
        for aid in considered_assumptions:
            cur.execute("INSERT INTO recommendation_assumption (recommendation_id, assumption_id, venture_id) VALUES (%s, %s, %s)", (rec_id, aid, venture_id))
        for tid in considered_tests:
            cur.execute("INSERT INTO recommendation_validation_test (recommendation_id, validation_test_id, venture_id) VALUES (%s, %s, %s)", (rec_id, tid, venture_id))
        for rid in considered_results:
            cur.execute("INSERT INTO recommendation_validation_result (recommendation_id, validation_result_id, venture_id) VALUES (%s, %s, %s)", (rec_id, rid, venture_id))
        audit.record_event(
            cur, event_type="nextaction.recommended", actor=actor, venture_id=venture_id,
            payload={"recommendation_id": str(rec_id), "opportunity_id": str(opportunity_id), "action_type": action, "reason": reason},
        )
    return Recommendation(rec_id, action, reason, created=True)


def get_recommendation(conn, recommendation_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, opportunity_id, action_type, dominant_reason_code, rationale, "
            "selected_validation_test_id, created_at FROM next_action_recommendation WHERE id = %s",
            (recommendation_id,),
        )
        return cur.fetchone()


def provenance(conn, recommendation_id: str) -> dict:
    """Structured basis: the exact assumptions/tests/results considered."""
    with conn.cursor() as cur:
        rec = get_recommendation(conn, recommendation_id)
        if rec is None:
            raise NotFoundError(f"recommendation {recommendation_id} does not exist")
        cur.execute(
            "SELECT a.id, a.importance FROM recommendation_assumption ra JOIN assumption a ON a.id = ra.assumption_id "
            "WHERE ra.recommendation_id = %s ORDER BY a.id",
            (recommendation_id,),
        )
        assumptions = [{"assumption_id": r[0], "importance": r[1]} for r in cur.fetchall()]
        cur.execute("SELECT validation_test_id FROM recommendation_validation_test WHERE recommendation_id = %s", (recommendation_id,))
        tests = [r[0] for r in cur.fetchall()]
        cur.execute(
            "SELECT r.id, r.outcome, r.wtp_modality, r.measurement_kind FROM recommendation_validation_result rr "
            "JOIN validation_result r ON r.id = rr.validation_result_id WHERE rr.recommendation_id = %s ORDER BY r.id",
            (recommendation_id,),
        )
        results = [{"result_id": r[0], "outcome": r[1], "wtp_modality": r[2], "measurement_kind": r[3]} for r in cur.fetchall()]
    return {
        "recommendation_id": rec[0], "opportunity_id": rec[2], "action_type": rec[3],
        "reason_code": rec[4], "selected_validation_test_id": rec[6],
        "considered_assumptions": assumptions, "considered_tests": tests, "considered_results": results,
    }


def latest_recommendation(conn, opportunity_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, action_type, dominant_reason_code FROM next_action_recommendation "
            "WHERE opportunity_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (opportunity_id,),
        )
        return cur.fetchone()
