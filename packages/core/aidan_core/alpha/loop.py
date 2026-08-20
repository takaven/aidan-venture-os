"""Loop-scoped Alpha classification (Gate 8 Slice 4).

Classifies ONE closed loop bounded by canonical lineage — no ``closed_loop_run`` table. The
boundary is reconstructed from existing state:

    start recommendation  ->  investment_decision_record (source_recommendation_id, resulting_action_id)
                          ->  market_action_spec  ->  VERIFIED MARKET_ACTION proof
                          ->  market_observation / market_window_completion  ->  next recommendation

The next recommendation is not trusted merely because it exists: it must belong to the same
venture, follow the start, AND cite (via recommendation provenance) an observation or no-response
completion of THIS loop's exact market action — otherwise it is rejected. ``start_at`` / ``end_at``
are canonical timestamps, so assistance is scoped strictly to the exact loop interval.

Three independent dimensions are returned — completeness, assistance (CLEAN vs HUMAN_ASSISTED),
and reality (REAL vs SIMULATED). REAL requires a REAL_PROVIDER action proof AND a deterministic
NO_RESPONSE completion anchored to it (a generic/unauthenticated observation NEVER confers REAL);
so a synthetic fixture is never ``eligible_clean_real_alpha`` (COMPLETE + CLEAN + REAL).
"""
from __future__ import annotations

from ..errors import MarketAuthorityError, NotFoundError
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


def _loop_spec(cur, action_request_id):
    cur.execute("SELECT id FROM market_action_spec WHERE action_request_id = %s", (action_request_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _cites_spec(cur, recommendation_id, spec_id) -> bool:
    """True iff the recommendation's exact provenance cites an observation OR a no-response
    completion of this market_action_spec — proof it consumed THIS loop's outcome."""
    cur.execute(
        "SELECT 1 FROM recommendation_market_observation rmo "
        "JOIN market_observation mo ON mo.id = rmo.observation_id "
        "WHERE rmo.recommendation_id = %s AND mo.market_action_spec_id = %s "
        "UNION SELECT 1 FROM recommendation_market_window_completion rmc "
        "JOIN market_window_completion mwc ON mwc.id = rmc.window_completion_id "
        "WHERE rmc.recommendation_id = %s AND mwc.market_action_spec_id = %s LIMIT 1",
        (recommendation_id, spec_id, recommendation_id, spec_id))
    return cur.fetchone() is not None


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

    Truth projection only — reads canonical state and writes nothing. Rejects a
    ``next_recommendation_id`` that is not the loop's actual next allocation.
    """
    with conn.cursor() as cur:
        venture_id, start_at = _rec(cur, start_recommendation_id)
        committed = _committed(cur, start_recommendation_id)
        decision = resulting_action_id = decided_at = None
        loop_spec_id = None
        if committed is not None:
            decision, resulting_action_id, decided_at = committed
            if resulting_action_id is not None:
                loop_spec_id = _loop_spec(cur, resulting_action_id)

        # bound the loop with the next recommendation — validated as THIS loop's next allocation.
        end_at = None
        next_exists = False
        if next_recommendation_id is not None:
            n_venture, n_at = _rec(cur, next_recommendation_id)
            if str(n_venture) != str(venture_id):
                raise MarketAuthorityError("next recommendation belongs to a different venture")
            if not (n_at > start_at):
                raise MarketAuthorityError("next recommendation does not follow the loop start")
            if loop_spec_id is None or not _cites_spec(cur, next_recommendation_id, loop_spec_id):
                raise MarketAuthorityError("next recommendation does not cite this loop's market outcome")
            end_at = n_at
            next_exists = True

        reality = SIMULATED
        if committed is None:
            completeness = INCOMPLETE
        elif decision in _TERMINAL:
            completeness = COMPLETE          # a terminal allocation (e.g. KILL) is a complete loop
            if end_at is None:
                end_at = decided_at
        elif resulting_action_id is not None:
            # REAL requires a REAL_PROVIDER action proof AND a NO_RESPONSE completion anchored to
            # it; a generic observation never confers REAL.
            if origin_mod.has_real_no_response(conn, loop_spec_id, resulting_action_id):
                reality = REAL
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
