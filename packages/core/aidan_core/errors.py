"""Canonical kernel error types."""
from __future__ import annotations


class AidanCoreError(Exception):
    """Base class for all kernel errors."""


class ConfigError(AidanCoreError):
    """Required configuration (e.g. DATABASE_URL) is absent or invalid."""


class MigrationError(AidanCoreError):
    """A migration could not be applied."""


class MigrationChecksumError(MigrationError):
    """An already-applied migration's checksum no longer matches its file.

    Forward-only doctrine: applied migrations must never be edited. A drift
    here is a hard failure, never a silent re-apply.
    """


class NotFoundError(AidanCoreError):
    """A referenced canonical entity does not exist."""


class IllegalTransitionError(AidanCoreError):
    """A venture lifecycle transition is not in the permitted set."""


class IdempotencyConflictError(AidanCoreError):
    """An idempotency key was reused with a different canonical payload.

    This is a hard, deterministic conflict: the earlier ActionRequest is never
    silently returned for a semantically different payload.
    """


class InsufficientBudgetError(AidanCoreError):
    """A reservation cannot be made because available budget is insufficient."""


class IllegalCapitalTransitionError(AidanCoreError):
    """An invalid capital transition (e.g. commit after release) was attempted."""


class ExecutionBlockedError(AidanCoreError):
    """Execution is prohibited by current policy (e.g. DENY / kill switch)."""


class ApprovalRequiredError(AidanCoreError):
    """No valid, non-expired approval exists for the current policy state."""


class ApprovalStateError(AidanCoreError):
    """An approval state change is invalid (e.g. approving a terminal approval)."""


class InconsistentCanonicalStateError(AidanCoreError):
    """Canonical success signals disagree (SUCCEEDED status vs VERIFIED proof).

    Raised rather than silently repairing or reporting success when exactly one
    of {status == SUCCEEDED, a VERIFIED proof receipt exists} is present.
    """


class InvalidAcquisitionError(AidanCoreError):
    """An acquired source (untrusted external input) failed validation."""


class EvidenceRelationConflictError(AidanCoreError):
    """The same claim/observation pair was asserted with a conflicting stance.

    A single observation cannot both support and contradict the same claim;
    the second, opposite-stance assertion is rejected deterministically rather
    than silently replacing the first.
    """


class OpportunityNotReadyError(AidanCoreError):
    """An opportunity cannot be finalized as CANDIDATE: structural completeness
    (hypotheses, linked claim(s)/assumption(s), critical unknown, and a complete
    adversarial Kill Case) is not yet satisfied.
    """


class MandateMismatchError(AidanCoreError):
    """Supplied Mandate content does not match the canonical mandate version hash.

    Research must execute against an exact Board-authored canonical Mandate
    version; a mismatch is rejected rather than proceeding or mutating it.
    """


class StaleRecommendationError(AidanCoreError):
    """A next-action recommendation no longer matches current canonical state.

    A recommendation is produced against an exact input basis (assumptions,
    validation tests/results). If materially new decision-relevant state exists
    when it is converted into an investment decision, the old recommendation is
    rejected as stale rather than committed. The prior recommendation is never
    mutated; a fresh recommendation must be obtained.
    """


class RecommendationNotConvertibleError(AidanCoreError):
    """A recommendation cannot be converted into the requested investment decision.

    Raised when a RESEARCH_MORE recommendation (which maps to no investment
    decision) is converted, or when an incompatible target decision is requested
    for a recommendation (e.g. KILL -> BUILD, BUILD -> SCALE).
    """


class BuildGateNotSatisfiedError(AidanCoreError):
    """A BUILD investment decision was refused by independent canonical re-gating.

    BUILD is never trusted because the allocator recommended it: current
    canonical state (opportunity status, positive provenance, complete Kill Case,
    unresolved critical assumptions, contextual WTP/acquisition validation,
    decisive kill criteria, unresolved contradictions) is re-verified, and the
    decision is refused if any required condition is not currently met.
    """


class ConsequentialSpendError(AidanCoreError):
    """A requested consequential amount is not permitted for this decision.

    For a VALIDATE decision the requested amount must be bounded by the selected
    validation test's precommitted ``max_spend``. A consequential amount with no
    precommitted bound, or one exceeding it, is rejected.
    """


class BuildAuthorityError(AidanCoreError):
    """A Gate 5 build-authority boundary was violated.

    Raised when builder dispatch is attempted without the required frozen build
    authority (a build_spec bound into the immutable execution spec, a registered
    isolated venture repository), when the bound build identity does not match the
    canonical rows, or when a builder would target the canonical OS repository.
    """


class DeployAuthorityError(AidanCoreError):
    """A Gate 6 deploy-authority boundary was violated.

    Raised when deployment dispatch is attempted without the required frozen deploy
    authority (a quality-qualified release_candidate bound into the immutable
    execution spec, a registered venture-owned deployment target), when the bound
    release identity/hash/target does not match the canonical rows, or when a
    release candidate is created without a passing Gate-5 overall quality verdict.
    """
