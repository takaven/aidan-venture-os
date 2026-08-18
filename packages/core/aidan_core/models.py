"""Canonical vocabularies.

Gate 1 keeps three *distinct* status concepts separate so they can never
collapse into one generic ``status`` field. The values here are intentionally
minimal placeholders sufficient to establish the type boundary; the final
state sets are not designed in Slice 1.

The separation is enforced at the database level by three distinct PostgreSQL
ENUM types (see ``migrations/0001_gate1_foundation.sql``). These Python enums
mirror those DB types for application-side use.

Invariant: ``lifecycle_state`` != ``run_status`` != ``investment_decision``.
"""
from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    """Where a venture is in its life. NOT a run status, NOT a decision."""

    DISCOVERED = "DISCOVERED"
    VALIDATING = "VALIDATING"
    BUILDING = "BUILDING"
    OPERATING = "OPERATING"
    ARCHIVED = "ARCHIVED"


class RunStatus(str, Enum):
    """Status of a unit of work/execution. NOT a lifecycle state."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InvestmentDecision(str, Enum):
    """An allocation decision/outcome. NOT a persistent lifecycle state.

    Per programme doctrine, decisions such as SCALE/KILL are recorded as
    decisions and must not be auto-persisted as lifecycle states.
    """

    VALIDATE = "VALIDATE"
    BUILD = "BUILD"
    IMPROVE = "IMPROVE"
    MARKET = "MARKET"
    SCALE = "SCALE"
    HOLD = "HOLD"
    KILL = "KILL"
    DO_NOTHING = "DO_NOTHING"


class PolicyOutcome(str, Enum):
    """Deterministic policy evaluation outcome (Slice 3)."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


# Names of the corresponding PostgreSQL ENUM types. Distinct DB types are the
# actual enforcement of vocabulary separation.
LIFECYCLE_STATE_TYPE = "lifecycle_state"
RUN_STATUS_TYPE = "run_status"
INVESTMENT_DECISION_TYPE = "investment_decision"
POLICY_DECISION_TYPE = "policy_decision_kind"
