"""Loop-scoped Alpha classification (Gate 8 Slice 4).

Classifies ONE closed loop bounded by canonical lineage — no ``closed_loop_run`` table. The
boundary is reconstructed from existing state:

    start recommendation  ->  investment_decision_record (source_recommendation_id, resulting_action_id)
                          ->  market_action_spec  ->  VERIFIED MARKET_ACTION proof
                          ->  market_observation / market_window_completion  ->  next recommendation

``start_at`` / ``end_at`` are canonical recommendation/decision timestamps, so autonomy assistance
is scoped to the exact loop interval: an intervention BEFORE the loop, AFTER it, or on another
venture never contaminates the classification. Three independent dimensions are returned —
completeness, assistance (CLEAN vs HUMAN_ASSISTED), and reality (REAL vs SIMULATED) — and a
synthetic (fixture) loop can never be ``eligible_clean_real_alpha`` because its reality is
SIMULATED.
"""
from __future__ import annotations

from ..errors import NotFoundError
from ..market import origin as origin_mod
from .autonomy import CLEAN, HUMAN_ASSISTED

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
REAL = origin_mod.REAL
SIMULATED = origin_mod.SIMULATED

_TERMINAL = ("KILL", "HOLD", "DO_NOTHING")


def _rec(cur, recommendation_id):
    cur.execute("SELECT venture_id, created_at FROM next_action_recommendation WHERE id = %s", (recommendation_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"recommendation {recommendation_id} does not exist")
    return row


def _committed(cur, start_recommendation_id):
    cur.execute(
        "SELECT decision, resulting_action_id, created_at FROM investment_decision_record "
        "WHERE source_recommendation_id = %s", (start_recommendation_id,))
    return cur.fetchone()


def _action_complete(cur, action_request_id):
    """A consequential market action is complete when it has a VERIFIED MARKET_ACTION proof and a
    canonical outcome (an observation or a deterministic no-response completion)."""
    cur.execute("SELECT id FROM market_action_spec WHERE action_request_id = %s", (action_request_id,))
    srow = cur.fetchone()
    if srow is None:
        return False
    spec_id = srow[0]
    cur.execute("SELECT 1 FROM proof_receipt WHERE action_request_id = %s AND verification_type = 'MARKET_ACTION' "
                "AND result = 'VERIFIED' LIMIT 1", (action_request_id,))
    if cur.fetchone() is None:
        return False
    cur.execute("SELECT (SELECT count(*) FROM market_observation WHERE market_action_spec_id = %s) "
                "+ (SELECT count(*) FROM market_window_completion WHERE market_action_spec_id = %s)",
                (spec_id, spec_id))
    return cur.fetchone()[0] > 0


def _interventions_in(cur, venture_id, start_at, end_at) -> int:
    if end_at is not None:
        cur.execute("SELECT count(*) FROM alpha_intervention WHERE venture_id = %s "
                    "AND occurred_at >= %s AND occurred_at < %s", (venture_id, start_at, end_at))
    else:
        cur.execute("SELECT count(*) FROM alpha_intervention WHERE venture_id = %s AND occurred_at >= %s",
                    (venture_id, start_at))
    return cur.fetchone()[0]


def classify_loop(conn, *, start_recommendation_id: str, next_recommendation_id=None) -> dict:
    """Deterministically classify the loop that starts at ``start_recommendation_id``.

    Truth projection only — reads canonical state and writes nothing.
    """
    with conn.cursor() as cur:
        venture_id, start_at = _rec(cur, start_recommendation_id)
        committed = _committed(cur, start_recommendation_id)

        # loop interval end: the next recommendation (open loop) or, for a terminal decision, the
        # decision instant. Assistance is scoped strictly to [start_at, end_at).
        end_at = None
        next_exists = False
        if next_recommendation_id is not None:
            nrow = _rec(cur, next_recommendation_id)
            end_at = nrow[1]
            next_exists = True

        reality = SIMULATED
        if committed is None:
            completeness = INCOMPLETE          # no canonical allocation decision yet
        else:
            decision, resulting_action_id, decided_at = committed
            if decision in _TERMINAL:
                completeness = COMPLETE        # a terminal allocation (e.g. KILL) is a complete loop
                if end_at is None:
                    end_at = decided_at
            elif resulting_action_id is not None:
                reality = origin_mod.action_reality(conn, resulting_action_id)
                completeness = COMPLETE if (_action_complete(cur, resulting_action_id) and next_exists) else INCOMPLETE
            else:
                completeness = INCOMPLETE

        assisted = _interventions_in(cur, venture_id, start_at, end_at) > 0
        assistance = HUMAN_ASSISTED if assisted else CLEAN

    eligible = completeness == COMPLETE and assistance == CLEAN and reality == REAL
    return {
        "completeness": completeness,
        "assistance_class": assistance,
        "reality_class": reality,
        "eligible_clean_real_alpha": eligible,
    }
