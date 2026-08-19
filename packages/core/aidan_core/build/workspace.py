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

# The canonical OS monorepo root (…/packages/core/aidan_core/build/workspace.py).
_OS_REPO_ROOT = Path(__file__).resolve().parents[4]


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_workspace(base_dir: str | None = None) -> str:
    """Create a fresh disposable venture workspace directory. Returns its path."""
    root = tempfile.mkdtemp(prefix="venture-ws-", dir=base_dir)
    assert_isolated_workspace(root)
    return root


def assert_isolated_workspace(root: str) -> None:
    """Reject a workspace root that is (or contains, or is inside) the OS monorepo."""
    r = Path(root).resolve()
    if r == _OS_REPO_ROOT or _OS_REPO_ROOT in r.parents or r in _OS_REPO_ROOT.parents:
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
