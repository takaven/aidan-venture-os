"""Deterministic market response-window completion (Gate 8 Slice 3).

A market action's response HORIZON is not a new field: it is derived from existing canonical
state — the window STARTS at the exact VERIFIED ``MARKET_ACTION`` proof_receipt timestamp and
lasts the precommitted Gate-3 ``validation_test.max_duration_days``. ``NO_RESPONSE`` is a
deterministic DERIVED fact (the deadline elapsed and no qualifying ``REPLIED`` was recorded by
it), never an external ``market_observation``. A late reply after the deadline remains real
evidence but does not retroactively make the deadline truth ``RESPONDED``.

Deriving/recording a completion is truth projection only: it creates no investment decision,
ActionRequest, policy decision, lifecycle transition, or capital entry, and mutates no
observation/proof/spec/validation_test.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import MarketAuthorityError, NotFoundError

# Window status values (derived, not stored on the action).
PENDING = "PENDING"
RESPONDED = "RESPONDED"
NO_RESPONSE = "NO_RESPONSE"
UNAVAILABLE = "UNAVAILABLE"   # no verified action proof, or no precommitted duration

# Only a REPLIED is a qualifying buyer response; DELIVERED/OPENED/CLICKED/BOUNCED/UNSUBSCRIBE
# are not replies and are never rewritten by window derivation.
_QUALIFYING = "REPLIED"


@dataclass(frozen=True)
class WindowStatus:
    status: str
    window_start_at: Optional[object] = None
    window_end_at: Optional[object] = None
    proof_receipt_id: Optional[str] = None
    validation_test_id: Optional[str] = None


def _window(cur, market_action_spec_id):
    """Resolve (venture, validation_test, verified proof, window_start, window_end) or None."""
    cur.execute(
        "SELECT id, venture_id, action_request_id, validation_test_id "
        "FROM market_action_spec WHERE id = %s", (market_action_spec_id,))
    spec = cur.fetchone()
    if spec is None:
        raise NotFoundError(f"market_action_spec {market_action_spec_id} does not exist")
    _sid, venture_id, action_request_id, validation_test_id = spec
    # exact VERIFIED MARKET_ACTION proof anchors the window start
    cur.execute(
        "SELECT id, created_at FROM proof_receipt WHERE action_request_id = %s "
        "AND verification_type = 'MARKET_ACTION' AND result = 'VERIFIED' ORDER BY created_at, id LIMIT 1",
        (action_request_id,))
    proof = cur.fetchone()
    if proof is None:
        return None
    proof_id, window_start = proof
    cur.execute("SELECT max_duration_days FROM validation_test WHERE id = %s", (validation_test_id,))
    row = cur.fetchone()
    max_days = row[0] if row is not None else None
    if max_days is None:
        return None  # no precommitted horizon -> not derivable
    window_end = window_start + timedelta(days=int(max_days))
    return dict(venture_id=str(venture_id), validation_test_id=str(validation_test_id),
                proof_receipt_id=str(proof_id), window_start=window_start, window_end=window_end)


def _qualifying_reply_by(cur, market_action_spec_id, deadline) -> bool:
    """True iff a canonical REPLIED occurred at/or before the deadline (occurred_at, else the
    ingestion time). Absence is established by querying canonical state — never a caller flag."""
    cur.execute(
        "SELECT count(*) FROM market_observation WHERE market_action_spec_id = %s "
        "AND observation_type = %s AND COALESCE(occurred_at, created_at) <= %s",
        (market_action_spec_id, _QUALIFYING, deadline))
    return cur.fetchone()[0] > 0


def market_window_status(conn, market_action_spec_id: str, *, as_of) -> WindowStatus:
    """Deterministically derive PENDING / RESPONDED / NO_RESPONSE / UNAVAILABLE at ``as_of``.

    ``as_of`` is an explicit timezone-aware instant (never the wall clock). Equality with the
    deadline counts as elapsed (``as_of >= window_end`` -> elapsed).
    """
    with conn.cursor() as cur:
        w = _window(cur, market_action_spec_id)
        if w is None:
            return WindowStatus(UNAVAILABLE)
        if _qualifying_reply_by(cur, market_action_spec_id, w["window_end"]):
            status = RESPONDED
        elif as_of >= w["window_end"]:
            status = NO_RESPONSE
        else:
            status = PENDING
        return WindowStatus(status, w["window_start"], w["window_end"], w["proof_receipt_id"], w["validation_test_id"])


@dataclass(frozen=True)
class CompletionResult:
    market_window_completion_id: str
    completion_hash: str
    created: bool


def _completion_hash(*, venture_id, market_action_spec_id, proof_receipt_id, validation_test_id,
                     window_start, window_end) -> str:
    return canonical_payload_hash({
        "venture_id": str(venture_id), "market_action_spec_id": str(market_action_spec_id),
        "proof_receipt_id": str(proof_receipt_id), "validation_test_id": str(validation_test_id),
        "window_start_at": str(window_start), "window_end_at": str(window_end),
        "completion_type": NO_RESPONSE,
    })


def record_no_response_completion(conn, market_action_spec_id: str, *, as_of, actor: str = "market") -> CompletionResult:
    """Persist the deterministic NO_RESPONSE completion fact, or converge if already recorded.

    Refuses to create it before the deadline elapses or when a qualifying response exists by the
    deadline. All timing/provenance is kernel-derived; no caller ``no_response=true`` is accepted.
    """
    with db.transaction(conn) as cur:
        w = _window(cur, market_action_spec_id)
        if w is None:
            raise MarketAuthorityError(
                "no-response is not derivable: the market action has no VERIFIED action proof or no "
                "precommitted validation_test.max_duration_days")
        if _qualifying_reply_by(cur, market_action_spec_id, w["window_end"]):
            raise MarketAuthorityError("a qualifying response occurred within the window; not NO_RESPONSE")
        if as_of < w["window_end"]:
            raise MarketAuthorityError("response window has not elapsed; NO_RESPONSE cannot be established yet")

        completion_hash = _completion_hash(
            venture_id=w["venture_id"], market_action_spec_id=market_action_spec_id,
            proof_receipt_id=w["proof_receipt_id"], validation_test_id=w["validation_test_id"],
            window_start=w["window_start"], window_end=w["window_end"])
        cur.execute(
            "SELECT id, completion_hash FROM market_window_completion "
            "WHERE market_action_spec_id = %s AND completion_type = %s", (market_action_spec_id, NO_RESPONSE))
        existing = cur.fetchone()
        if existing is not None:
            return CompletionResult(str(existing[0]), existing[1], created=False)
        cur.execute(
            """
            INSERT INTO market_window_completion
                (venture_id, market_action_spec_id, proof_receipt_id, validation_test_id,
                 window_start_at, window_end_at, completion_type, completion_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (w["venture_id"], market_action_spec_id, w["proof_receipt_id"], w["validation_test_id"],
             w["window_start"], w["window_end"], NO_RESPONSE, completion_hash))
        cid = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="market.no_response_completed", actor=actor, venture_id=w["venture_id"],
            payload={"market_window_completion_id": str(cid), "market_action_spec_id": str(market_action_spec_id)})
    return CompletionResult(str(cid), completion_hash, created=True)


def no_response_completion_for(conn, market_action_spec_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, window_start_at, window_end_at, completion_hash FROM market_window_completion "
            "WHERE market_action_spec_id = %s AND completion_type = %s", (market_action_spec_id, NO_RESPONSE))
        return cur.fetchone()
