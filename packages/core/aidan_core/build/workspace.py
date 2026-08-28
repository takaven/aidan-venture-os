"""Root-contained venture workspace primitives (Gate 5 Slice 2).

The smallest REAL filesystem boundary needed to materialize and inspect candidate
build output safely. A venture workspace is a disposable directory tree; file
materialization is **root-contained** to that directory. This is deliberately NOT
a process/code sandbox: Slice 2 executes no arbitrary Builder code on the host
(see ``technical.py``). The claim is only "file materialization is root-contained
to the designated venture workspace", never "arbitrary untrusted code is securely
sandboxed".
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from ..errors import BuildAuthorityError

# Trusted, installation-independent identity of the canonical OS monorepo. The old
# ``Path(__file__).resolve().parents[4]`` identified the repo only from a source
# checkout — from an installed wheel it resolved to the virtualenv root, so the
# guard silently FAILED OPEN on the real repository. Identity now comes from
# explicit host-runtime configuration (owned by the trusted layer, never by worker
# or task-payload input), with a source-checkout fallback that self-disables under
# an installed wheel so it can never mis-identify an unrelated directory.
_OS_REPO_ROOT_ENV = "AIDAN_OS_REPO_ROOT"


def canonical_os_repo_root() -> Path | None:
    """The canonical AIDAN OS repository root, or ``None`` if it cannot be trusted.

    Resolution: the explicit ``AIDAN_OS_REPO_ROOT`` host-runtime configuration
    (installation-independent), else a source-checkout fallback that returns the
    monorepo root ONLY when this module is actually running from the source tree
    (verified by the presence of ``packages/core/aidan_core`` beside it). From an
    installed wheel the fallback finds no such sibling and returns ``None`` rather
    than pointing at the virtualenv.
    """
    env = os.environ.get(_OS_REPO_ROOT_ENV)
    if env:
        return Path(env).resolve()
    candidate = Path(__file__).resolve().parents[4]
    if (candidate / "packages" / "core" / "aidan_core").is_dir():
        return candidate
    return None


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_workspace(base_dir: str | None = None, *, require_canonical: bool = False) -> str:
    """Create a fresh disposable venture workspace directory. Returns its path."""
    root = tempfile.mkdtemp(prefix="venture-ws-", dir=base_dir)
    assert_isolated_workspace(root, require_canonical=require_canonical)
    return root


def assert_isolated_workspace(root: str, *, require_canonical: bool = False) -> None:
    """Reject a workspace root that is (or contains, or is inside) the OS monorepo.

    The canonical root comes from trusted host-runtime identity
    (:func:`canonical_os_repo_root`), so this guard protects the real repository
    from an installed wheel too — provided the host configured it. When identity
    is unavailable, a freshly created disposable workspace is still safe by
    construction, so the default is permissive; pass ``require_canonical=True`` at
    a consequential build boundary to fail CLOSED unless the canonical root is
    trusted and known.
    """
    r = Path(root).resolve()
    canonical = canonical_os_repo_root()
    if canonical is None:
        if require_canonical:
            raise BuildAuthorityError(
                "canonical OS repository root is not configured; set "
                f"{_OS_REPO_ROOT_ENV} so the workspace guard can fail closed"
            )
        return
    if r == canonical or canonical in r.parents or r in canonical.parents:
        raise BuildAuthorityError(
            f"workspace {root!r} resolves into the canonical OS repository tree"
        )


def _normalize_relpath(relpath: str) -> str:
    p = str(relpath).replace("\\", "/").strip()
    if not p:
        raise BuildAuthorityError("empty candidate path")
    if p.startswith("/") or (len(p) >= 2 and p[1] == ":"):
        raise BuildAuthorityError(f"absolute candidate path is not allowed: {relpath!r}")
    while p.startswith("./"):        # strip a leading "./" only; never collapse ".." away
        p = p[2:]
    if not p:
        raise BuildAuthorityError("empty candidate path")
    return p


def contained_path(root: str, relpath: str) -> Path:
    """Resolve ``relpath`` under ``root``, rejecting any escape.

    Rejects absolute paths, ``..`` traversal, and symlink escapes (via realpath),
    so a materialized file can never leave its venture workspace root.
    """
    norm = _normalize_relpath(relpath)
    root_real = Path(root).resolve()
    target = (root_real / norm).resolve()
    if target != root_real and root_real not in target.parents:
        raise BuildAuthorityError(f"candidate path escapes the venture workspace: {relpath!r}")
    return target


def write_candidate(root: str, relpath: str, content: bytes) -> dict:
    """Write one candidate file under the workspace root (contained). Returns kernel metadata."""
    target = contained_path(root, relpath)
    # Guard against symlink-escape: the parent must resolve back under root.
    target.parent.mkdir(parents=True, exist_ok=True)
    real_parent = Path(os.path.realpath(target.parent))
    root_real = Path(root).resolve()
    if real_parent != root_real and root_real not in real_parent.parents:
        raise BuildAuthorityError(f"candidate parent escapes the venture workspace: {relpath!r}")
    target.write_bytes(content)
    norm = _normalize_relpath(relpath)
    return {"path": norm, "sha256": hash_bytes(content), "size": len(content)}


def read_hash(root: str, relpath: str) -> dict:
    """Re-read a materialized file and return kernel-computed metadata (for integrity checks)."""
    target = contained_path(root, relpath)
    content = target.read_bytes()
    return {"path": _normalize_relpath(relpath), "sha256": hash_bytes(content), "size": len(content)}
