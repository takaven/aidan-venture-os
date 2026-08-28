"""Deterministic forward-only SQL migration runner.

Doctrine:
- forward-only: no downgrades, no editing an applied migration;
- deterministic order: numeric prefix ``NNNN_name.sql``;
- checksum-locked: an already-applied migration whose file changed is a hard
  failure, never a silent re-apply;
- transactional: a migration and its ``schema_migrations`` record commit
  together, so a failed migration is never recorded as applied;
- idempotent: re-running applies nothing when up to date.
"""
from __future__ import annotations

import hashlib
import os
from importlib import resources
from pathlib import Path
from typing import Optional

from . import db
from .errors import MigrationChecksumError, MigrationError

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def default_migrations_dir() -> Path:
    """Resolve the migrations directory, installation-independently.

    Order: explicit ``MIGRATIONS_DIR`` override, else the canonical migrations
    shipped as package resources inside ``aidan_core.migrations`` (the single
    source of truth). Resolving via ``importlib.resources`` means the installed
    wheel bootstraps canonical state with no repository checkout and no reliance
    on the caller's cwd. In an ordinary (unpacked) install these resources are a
    real directory on disk, so ``Path.glob`` over them works unchanged.
    """
    env = os.environ.get("MIGRATIONS_DIR")
    if env:
        return Path(env)
    return Path(resources.files("aidan_core.migrations"))


def _checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def discover(migrations_dir: Optional[Path] = None) -> list[tuple[str, str, str, str]]:
    """Return ``(version, name, checksum, sql)`` tuples in deterministic order."""
    directory = Path(migrations_dir) if migrations_dir else default_migrations_dir()
    files = [p for p in directory.glob("[0-9]*.sql") if p.is_file()]
    files.sort(key=lambda p: int(p.name.split("_", 1)[0]))
    result: list[tuple[str, str, str, str]] = []
    for path in files:
        raw = path.read_bytes()
        version = path.name.split("_", 1)[0]
        result.append((version, path.name, _checksum(raw), raw.decode("utf-8")))
    return result


def apply(conn, migrations_dir: Optional[Path] = None) -> list[str]:
    """Apply all pending migrations. Return the versions applied this run."""
    with db.transaction(conn) as cur:
        cur.execute(BOOTSTRAP_SQL)

    applied_now: list[str] = []
    for version, name, checksum, sql in discover(migrations_dir):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT checksum FROM schema_migrations WHERE version = %s",
                (version,),
            )
            row = cur.fetchone()

        if row is not None:
            if row[0] != checksum:
                raise MigrationChecksumError(
                    f"checksum drift for applied migration {version} ({name}); "
                    "applied migrations are immutable"
                )
            continue  # already applied with matching checksum -> safe no-op

        try:
            with db.transaction(conn) as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) "
                    "VALUES (%s, %s, %s)",
                    (version, name, checksum),
                )
        except MigrationChecksumError:
            raise
        except Exception as exc:  # migration SQL failed -> tx rolled back, not recorded
            raise MigrationError(f"migration {version} ({name}) failed: {exc}") from exc
        applied_now.append(version)

    return applied_now


def main() -> int:
    """CLI entrypoint: apply migrations against ``DATABASE_URL``."""
    conn = db.connect(autocommit=True)
    try:
        applied = apply(conn)
        print(f"migrations applied: {applied if applied else 'none (up to date)'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
