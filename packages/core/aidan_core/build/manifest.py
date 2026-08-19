"""Durable build manifest — kernel-derived candidate provenance (Gate 5 Slice 2).

A ``build_manifest`` records, for one exact execution attempt, the candidate product
output a builder produced: the materialized venture-workspace file tree with
KERNEL-COMPUTED hashes (a worker cannot lie about a file hash — the kernel writes
and re-hashes the actual bytes). It is provenance, NOT a quality verdict. It is
immutable and one-per-attempt; an identical re-capture converges, any materially
changed candidate/substrate/binding is a hard conflict. Retries therefore never
overwrite earlier build history.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import BuildAuthorityError, IdempotencyConflictError, NotFoundError
from . import repository as repo_mod
from . import spec as build_spec_mod
from . import substrate as substrate_mod
from . import workspace as ws


@dataclass(frozen=True)
class ManifestResult:
    build_manifest_id: str
    manifest_hash: str
    candidate_tree_hash: str
    file_manifest: list
    workspace_root: str
    created: bool


def _to_bytes(content: Any) -> bytes:
    if isinstance(content, bytes):
        return content
    return str(content).encode("utf-8")


def _norm_path(workspace_root: str, path: str) -> str:
    root_real = Path(workspace_root).resolve()
    target = ws.contained_path(workspace_root, path)
    return "." if target == root_real else target.relative_to(root_real).as_posix()


def capture_build_manifest(
    conn,
    action_request_id: str,
    *,
    execution_attempt_id: str,
    substrate_release_id: str,
    candidate_files: list,
    workspace_root: str,
    source_root: Optional[Path] = None,
    actor: str = "factory",
) -> ManifestResult:
    """Materialize substrate + candidate files into the venture workspace and freeze
    the immutable, kernel-hashed manifest for this exact attempt."""
    build_row = build_spec_mod.get_build_spec(conn, action_request_id)
    if build_row is None:
        raise BuildAuthorityError(
            f"no frozen build_spec for action {action_request_id}; cannot capture a build manifest"
        )
    build_spec_id, venture_id = build_row[0], build_row[1]

    repo_row = repo_mod.get_venture_repository(conn, venture_id)
    if repo_row is None:
        raise BuildAuthorityError(f"venture {venture_id} has no registered venture_repository")
    venture_repository_id = repo_row[0]

    release_row = substrate_mod.get_substrate_release(conn, substrate_release_id)
    if release_row is None:
        raise NotFoundError(f"substrate_release {substrate_release_id} does not exist")

    ws.assert_isolated_workspace(workspace_root)
    # Substrate first (infrastructure), then venture-specific candidate files.
    entries: list[dict] = substrate_mod.materialize_substrate(
        release_row, workspace_root, source_root=source_root
    )
    seen: dict[str, str] = {e["path"]: e["sha256"] for e in entries}

    for decl in candidate_files or []:
        content = _to_bytes(decl["content"])          # kernel owns the bytes; worker-declared hash ignored
        norm = _norm_path(workspace_root, decl["path"])
        sha = ws.hash_bytes(content)
        if norm in seen:
            if seen[norm] != sha:
                raise BuildAuthorityError(
                    f"candidate path {norm!r} conflicts with existing workspace content"
                )
            continue                                   # identical duplicate converges
        meta = ws.write_candidate(workspace_root, decl["path"], content)
        meta["origin"] = "candidate"
        entries.append(meta)
        seen[norm] = sha

    entries.sort(key=lambda e: e["path"])
    candidate_tree_hash = canonical_payload_hash({
        "files": [{"path": e["path"], "sha256": e["sha256"]} for e in entries]
    })
    manifest_hash = canonical_payload_hash({
        "venture_id": str(venture_id),
        "action_request_id": str(action_request_id),
        "execution_attempt_id": str(execution_attempt_id),
        "build_spec_id": str(build_spec_id),
        "venture_repository_id": str(venture_repository_id),
        "substrate_release_id": str(substrate_release_id),
        "candidate_tree_hash": candidate_tree_hash,
    })

    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT id, manifest_hash FROM build_manifest WHERE execution_attempt_id = %s",
            (execution_attempt_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != manifest_hash:
                raise IdempotencyConflictError(
                    f"build_manifest for attempt {execution_attempt_id} already exists with "
                    "different candidate content"
                )
            return ManifestResult(existing[0], manifest_hash, candidate_tree_hash, entries,
                                  workspace_root, created=False)
        cur.execute(
            """
            INSERT INTO build_manifest
                (venture_id, action_request_id, execution_attempt_id, build_spec_id,
                 venture_repository_id, substrate_release_id, candidate_tree_hash, file_manifest, manifest_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, action_request_id, execution_attempt_id, build_spec_id,
             venture_repository_id, substrate_release_id, candidate_tree_hash, Json(entries), manifest_hash),
        )
        manifest_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="build.build_manifest_captured", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"build_manifest_id": str(manifest_id), "execution_attempt_id": str(execution_attempt_id),
                     "manifest_hash": manifest_hash},
        )
    return ManifestResult(manifest_id, manifest_hash, candidate_tree_hash, entries, workspace_root, created=True)


def get_build_manifest(conn, execution_attempt_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, action_request_id, execution_attempt_id, build_spec_id, "
            "venture_repository_id, substrate_release_id, candidate_tree_hash, file_manifest, "
            "manifest_hash, created_at FROM build_manifest WHERE execution_attempt_id = %s",
            (execution_attempt_id,),
        )
        return cur.fetchone()
