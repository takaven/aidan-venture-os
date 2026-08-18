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
