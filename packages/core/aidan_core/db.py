"""Smallest safe PostgreSQL connectivity and transaction boundary.

This module is deliberately minimal: it is not a repository framework and it
does not model any future worker/agent access. ``psycopg`` is imported lazily
so that importing :mod:`aidan_core` does not require the driver to be present
(useful for pure-logic tests in environments without the binary).
"""
from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Iterator, Optional

from .errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import psycopg


def get_dsn() -> str:
    """Return the canonical database DSN from ``DATABASE_URL``.

    Raises :class:`ConfigError` if it is not configured. Credentials are never
    hard-coded and never logged.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ConfigError("DATABASE_URL is not set")
    return dsn


def connect(dsn: Optional[str] = None, *, autocommit: bool = False) -> "psycopg.Connection":
    """Open a new connection to the canonical database.

    The driver is imported here (lazily) rather than at module import time.
    """
    import psycopg

    return psycopg.connect(dsn or get_dsn(), autocommit=autocommit)


@contextlib.contextmanager
def transaction(conn: "psycopg.Connection") -> Iterator["psycopg.Cursor"]:
    """Run a block inside one explicit transaction.

    Commits on success, rolls back on exception. Works whether or not the
    connection is in autocommit mode, because it uses psycopg's native
    transaction block to bracket an explicit ``BEGIN``/``COMMIT``.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            yield cur
