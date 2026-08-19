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
    """Reduce common Git/path representations of a repository to a comparable identity.

    Handles (case-insensitively): backslashes, trailing slashes, a URL scheme
    (``https://``, ``ssh://``, ``git://``), scp-style ``git@host:owner/repo``, a
    trailing ``.git`` file or ``/.git`` directory. Identity comparison only — NOT a
    claim of filesystem-level sandboxing.
    """
    norm = ref.strip().replace("\\", "/").lower()
    if "://" in norm:                       # strip a URL scheme (https://, ssh://, git://)
        norm = norm.split("://", 1)[1]
    elif "@" in norm and ":" in norm:       # scp-style git@host:owner/repo
        after_user = norm.split("@", 1)[1]
        host, _, path = after_user.partition(":")
        norm = f"{host}/{path}"
    norm = norm.rstrip("/")
    if norm.endswith("/.git"):              # a bare/checkout .git directory
        norm = norm[:-len("/.git")]
    elif norm.endswith(".git"):             # repo.git
        norm = norm[:-len(".git")]
    return norm.rstrip("/")


def assert_isolated_repository_ref(repository_ref: str) -> None:
    """Reject an empty ref or one that resolves to the canonical OS repository.

    Kernel-level identity protection: raises :class:`BuildAuthorityError` if the
    ref names the OS monorepo by any known identifier under common Git/path
    representations (exact, final path segment, or ``owner/repo`` tail). It rejects
    the OS repo itself, not unrelated names that merely contain it (e.g. a
    ``my-aidan-venture-os-fork`` is allowed). This is identity normalization, NOT
    filesystem sandboxing.
    """
    if not repository_ref or not repository_ref.strip():
        raise ValueError("repository_ref is required")
    norm = _normalize_ref(repository_ref)
    segments = norm.split("/")
    last1 = segments[-1] if segments else norm
    last2 = "/".join(segments[-2:]) if len(segments) >= 2 else norm
    for marker in CANONICAL_OS_REPOSITORY_MARKERS:
        m = marker.lower()
        if norm == m or last1 == m or last2 == m:
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
            "SELECT id, repository_ref, repository_scheme, provenance "
            "FROM venture_repository WHERE venture_id = %s",
            (venture_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            # Exact replay converges; ANY materially changed immutable field conflicts
            # (a repository identity is never silently reassigned or re-annotated).
            if (existing[1], existing[2], existing[3] or {}) != (
                repository_ref, repository_scheme, provenance
            ):
                raise IdempotencyConflictError(
                    f"venture {venture_id} already has repository {existing[1]!r} "
                    f"(scheme={existing[2]!r}); cannot change registration to "
                    f"{repository_ref!r} (scheme={repository_scheme!r})"
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
