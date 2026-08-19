"""Governed market action dispatch composition (Gate 7 Slice 1).

No second market/execution runtime: a channel worker is a replaceable Gate 4
``WorkerAdapter``. Market dispatch composes the frozen ``market_action_spec`` onto the
EXISTING Gate 4 path — it binds the spec id + action_spec_hash + channel + audience +
exact content into the immutable ``execution_spec`` (whose creation independently
enforces the market-authority guard for ANY caller), then dispatches through
``factory.runtime.execute_action``.

Slice 1 performs NO external send, NO observation, and NO market proof: dispatch (a
deterministic fake worker) captures the worker's result as a CLAIM only. Authorization
is obtained by the Gate 4 runtime only after the market intent is frozen into the
immutable execution spec; the historical/pre-spec authorization cannot authorize it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .. import policy
from ..errors import MarketAuthorityError
from ..factory import runtime as factory_runtime
from ..factory import spec as spec_mod
from ..factory.workers import WorkerRegistry
from . import action as action_mod

MARKET_CAPABILITIES = ("SEND_OUTREACH",)


@dataclass(frozen=True)
class MarketActionInput:
    """Typed market-specific data carried inside the canonical WorkerRequest.

    A channel ``WorkerAdapter`` reconstructs this from ``request.task_payload['market']``.
    It gives the worker the EXACT frozen intent to execute — no DB access, and no
    authority to substitute channel/audience/content/price/spend.
    """

    venture_id: str
    market_action_spec_id: str
    action_spec_hash: str
    channel_kind: str
    audience_ref: str
    content: str
    offer_ref: Any
    authorized_spend_amount: str

    @classmethod
    def from_worker_request(cls, request) -> "MarketActionInput":
        block = dict((request.task_payload or {}).get("market", {}))
        if not block:
            raise MarketAuthorityError("worker request carries no bound market authority")
        return cls(
            venture_id=str(request.venture_id),
            market_action_spec_id=str(block["market_action_spec_id"]),
            action_spec_hash=str(block["action_spec_hash"]),
            channel_kind=str(block["channel_kind"]),
            audience_ref=str(block["audience_ref"]),
            content=str(block["content"]),
            offer_ref=block.get("offer_ref"),
            authorized_spend_amount=str(block.get("authorized_spend_amount", "0")))


@dataclass(frozen=True)
class MarketDispatch:
    action_request_id: str
    market_action_spec_id: str
    action_spec_hash: str
    channel_kind: str
    execution_spec_id: str
    execution_spec_created: bool


def _market_task_payload(ms_row) -> dict:
    (_id, _v, _arid, _opp, _vt, channel_kind, audience_ref, _prov, content, content_hash,
     offer_ref, price_amount, price_currency, offer_terms, authorized_spend, spend_currency,
     action_spec_hash, _created) = ms_row
    return {
        "market": {
            "market_action_spec_id": str(_id),
            "action_spec_hash": action_spec_hash,
            "channel_kind": channel_kind,
            "audience_ref": audience_ref,
            "content": content,
            "content_hash": content_hash,
            "offer_ref": offer_ref,
            "price_amount": None if price_amount is None else str(price_amount),
            "price_currency": price_currency,
            "offer_terms": offer_terms,
            "authorized_spend_amount": str(authorized_spend),
            "spend_currency": spend_currency,
        }
    }


def prepare_market_execution(
    conn,
    action_request_id: str,
    *,
    worker_kind: str,
    verifier_kind: str,
    timeout_seconds: int = 60,
    max_attempts: int = 1,
    capability_scope=MARKET_CAPABILITIES,
    actor: str = "market",
) -> MarketDispatch:
    """Bind the frozen market_action_spec into an immutable Gate-4 execution spec.
    Idempotent; does not dispatch."""
    ms_row = action_mod.get_market_action_spec(conn, action_request_id)
    if ms_row is None:
        raise MarketAuthorityError(
            f"no frozen market_action_spec for action {action_request_id}; "
            "a market execution cannot be prepared from free-form intent")
    task_payload = _market_task_payload(ms_row)
    spec = spec_mod.create_execution_spec(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=list(capability_scope), task_payload=task_payload,
        expected_output_contract={"market": {"content_hash": ms_row[9]}}, actor=actor)
    return MarketDispatch(
        action_request_id=str(action_request_id), market_action_spec_id=str(ms_row[0]),
        action_spec_hash=ms_row[16], channel_kind=ms_row[5],
        execution_spec_id=str(spec.spec_id), execution_spec_created=spec.created)


def execute_market_action(
    conn,
    action_request_id: str,
    *,
    registry: WorkerRegistry,
    worker_kind: str,
    verifier_kind: str = "structured-contract",
    timeout_seconds: int = 60,
    max_attempts: int = 1,
    capability_scope=MARKET_CAPABILITIES,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    safety_mode: str = "IDEMPOTENT",
    lease_seconds: float = 300,
    clock=None,
    actor: str = "market",
):
    """Bind market authority and dispatch the channel worker through the Gate 4 runtime.

    Slice 1 stops at result capture: NO external send, NO observation, NO market proof.
    The worker result is a CLAIM only. Fresh post-spec authorization is obtained by the
    Gate 4 runtime.
    """
    dispatch = prepare_market_execution(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=capability_scope, actor=actor)
    result = factory_runtime.execute_action(
        conn, action_request_id, registry=registry, workspace_ref=f"market://{dispatch.channel_kind}",
        approval_threshold=approval_threshold, safety_mode=safety_mode,
        lease_seconds=lease_seconds, clock=clock, actor=actor)
    return dispatch, result
