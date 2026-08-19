"""Isolated venture repository identity (Gate 5 Slice 1).

A ``venture_repository`` names the isolated source-control boundary in which one
venture's product is built. Slice 1 establishes IDENTITY and AUTHORITY only:
there is no GitHub provisioning, no clone/worktree mechanics, and no network —
those enter Slice 2 when the Builder begins producing candidate files.

Two isolation guarantees live here:

* DB: one canonical product repository per venture, and a repository backs at
  most one venture (``UNIQUE(venture_id)`` + ``UNIQUE(repository_ref)``);
  registration is immutable, so a repo can never be silently reassigned.
* Kernel: a builder may NEVER target the canonical OS monorepo. This is enforced
  in trusted code (:func:`assert_isolated_repository_ref`) rather than by a DB
  string CHECK, which could only PRETEND that an opaque ref identifies the OS
  repo. It is a guard against the known OS-repo identifiers — not a claim of
  filesystem-level sandboxing (that is a later concern).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..errors import BuildAuthorityError, IdempotencyConflictError, NotFoundError

# Known identifiers of the canonical OS monorepo. A venture product repository may
# never resolve to any of these. Extend if the OS repo gains additional canonical
# names/remotes; this is trusted kernel configuration.
CANONICAL_OS_REPOSITORY_MARKERS = frozenset({
    "takaven/aidan-venture-os",
    "aidan-venture-os",
    "aidan-venture-os-gate0-source",
})


def _normalize_ref(ref: str) -> str:
    return ref.strip().replace("\\", "/").rstrip("/").lower()


def assert_isolated_repository_ref(repository_ref: str) -> None:
    """Reject an empty ref or one that resolves to the canonical OS repository.

    Kernel-level protection: raises :class:`ValidationError` if the ref names the
    OS monorepo by any known identifier (exact, or as the final path segment).
    """
    if not repository_ref or not repository_ref.strip():
        raise ValueError("repository_ref is required")
    norm = _normalize_ref(repository_ref)
    last = norm.rsplit("/", 1)[-1]
    for marker in CANONICAL_OS_REPOSITORY_MARKERS:
        m = marker.lower()
        if norm == m or last == m or norm.endswith("/" + m):
            raise BuildAuthorityError(
                "a builder may not target the canonical OS repository "
                f"({repository_ref!r} resolves to {marker!r})"
            )


@dataclass(frozen=True)
class RepositoryResult:
    venture_repository_id: str
    repository_ref: str
    created: bool


def register_venture_repository(
    conn,
    venture_id: str,
    *,
    repository_ref: str,
    repository_scheme: str = "mock",
    provenance: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> RepositoryResult:
    """Register the one isolated product repository for a venture. Idempotent.

    Exact re-registration (same venture, same ref) converges; the same venture
    with a materially different ref is a deterministic
    :class:`IdempotencyConflictError` (a repository identity is never silently
    reassigned). The canonical OS repository is rejected outright.
    """
    assert_isolated_repository_ref(repository_ref)
    provenance = provenance or {}

    with db.transaction(conn) as cur:
        cur.execute("SELECT 1 FROM venture WHERE id = %s", (venture_id,))
        if cur.fetchone() is None:
            raise NotFoundError(f"venture {venture_id} does not exist")

        cur.execute(
            "SELECT id, repository_ref FROM venture_repository WHERE venture_id = %s",
            (venture_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != repository_ref:
                raise IdempotencyConflictError(
                    f"venture {venture_id} already has repository {existing[1]!r}; "
                    f"cannot reassign to {repository_ref!r}"
                )
            return RepositoryResult(existing[0], existing[1], created=False)

        cur.execute(
            """
            INSERT INTO venture_repository (venture_id, repository_ref, repository_scheme, provenance)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (venture_id, repository_ref, repository_scheme, Json(provenance)),
        )
        repo_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="build.venture_repository_registered", actor=actor,
            venture_id=venture_id, action_id=None,
            payload={"venture_repository_id": str(repo_id), "repository_ref": repository_ref},
        )
    return RepositoryResult(repo_id, repository_ref, created=True)


def get_venture_repository(conn, venture_id: str):
    """Return the venture's registered repository row, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, repository_ref, repository_scheme, provenance, created_at "
            "FROM venture_repository WHERE venture_id = %s",
            (venture_id,),
        )
        return cur.fetchone()
