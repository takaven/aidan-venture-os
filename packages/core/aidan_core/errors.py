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
