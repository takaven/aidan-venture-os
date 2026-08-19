"""Gate 7 — governed Market Runtime.

Slice 1 establishes the authority boundary between an OPERATING venture and any external
market action: an immutable ``market_action_spec`` (1:1 with the market ActionRequest)
that freezes exact channel/audience/content/offer/spend and binds Gate-2/3 commercial
provenance, plus a composition that binds it into the existing Gate 4 execution runtime.
The channel worker is an ordinary Gate 4 ``WorkerAdapter`` — no second runtime.

No external send, no market observation, no interpretation, no market proof, and no
investment/lifecycle mutation live in Slice 1.
"""
from __future__ import annotations

from .action import (
    MARKET_ACTION_TYPE,
    MarketActionResult,
    create_market_action_spec,
    get_market_action_spec,
)
from .runtime import (
    MarketActionInput,
    MarketDispatch,
    execute_market_action,
    prepare_market_execution,
)

__all__ = [
    "MARKET_ACTION_TYPE",
    "MarketActionResult",
    "create_market_action_spec",
    "get_market_action_spec",
    "MarketActionInput",
    "MarketDispatch",
    "prepare_market_execution",
    "execute_market_action",
]
