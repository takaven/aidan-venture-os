"""Durable execution artifacts — worker output provenance, not success.

The kernel captures a worker's declared outputs as append-only ``execution_artifact``
rows bound to the exact execution attempt. Two boundaries are load-bearing:

- the content hash is computed BY THE KERNEL from the declared content; a
  worker-declared hash is never trusted or stored;
- an artifact reference must resolve within the worker's isolated workspace — a
  reference containing path traversal is rejected (no filesystem dereference is
  performed in this slice; refs are opaque identifiers over in-memory content).

Artifact existence never implies verification success.
"""
from __future__ import annotations

import hashlib
from typing import Any

from psycopg.types.json import Json

from .. import audit, db
from ..errors import IdempotencyConflictError

_ARTIFACT_TYPES = frozenset(
    {"STRUCTURED_RESULT", "FILE", "PATCH", "TEST_REPORT", "LOG_REFERENCE", "OTHER"}
)


def content_hash(content: str) -> str:
    """Deterministic kernel-computed digest of artifact content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_ref(ref: str) -> None:
    """Reject references that could escape the isolated workspace boundary."""
    if not ref:
        raise ValueError("artifact_ref is required")
    segments = ref.replace("\\", "/").split("/")
    if ".." in segments or ref.startswith("/") or ref.startswith("\\") or (len(ref) > 1 and ref[1] == ":"):
        raise ValueError(f"unsafe artifact_ref {ref!r}: path traversal / absolute paths are not permitted")


def capture_artifacts(
    conn, *, action_request_id: str, execution_attempt_id: str, venture_id: str,
    declarations: list[dict[str, Any]], actor: str = "factory",
) -> list[str]:
    """Persist worker-declared artifacts for an attempt. Idempotent per artifact_key.

    Each declaration is ``{artifact_key, artifact_type, ref, content[, metadata]}``.
    The content is hashed by the kernel; the worker cannot assert the hash.
    Returns the list of artifact ids (existing ids on idempotent re-capture).
    """
    ids: list[str] = []
    with db.transaction(conn) as cur:
        for decl in declarations:
            key = decl.get("artifact_key")
            atype = decl.get("artifact_type")
            ref = decl.get("ref")
            content = decl.get("content")
            if not key:
                raise ValueError("artifact_key is required")
            if atype not in _ARTIFACT_TYPES:
                raise ValueError(f"artifact_type must be one of {sorted(_ARTIFACT_TYPES)}")
            if not isinstance(content, str):
                raise ValueError(f"artifact {key!r} requires string content for kernel hashing")
            _validate_ref(ref)
            digest = content_hash(content)          # kernel-computed; worker-declared hash ignored
            size = len(content.encode("utf-8"))
            metadata = decl.get("metadata") or {}

            cur.execute(
                """
                INSERT INTO execution_artifact
                    (venture_id, action_request_id, execution_attempt_id, artifact_key,
                     artifact_type, artifact_ref, content_hash, size_bytes, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (execution_attempt_id, artifact_key) DO NOTHING
                RETURNING id
                """,
                (venture_id, action_request_id, execution_attempt_id, key, atype, ref,
                 digest, size, Json(metadata)),
            )
            row = cur.fetchone()
            if row is not None:
                ids.append(row[0])
                audit.record_event(
                    cur, event_type="factory.artifact_captured", actor=actor, venture_id=venture_id,
                    action_id=action_request_id,
                    payload={"artifact_key": key, "artifact_type": atype, "content_hash": digest},
                )
                continue
            # Existing key: converge only if the content hash is identical.
            cur.execute(
                "SELECT id, content_hash FROM execution_artifact "
                "WHERE execution_attempt_id = %s AND artifact_key = %s",
                (execution_attempt_id, key),
            )
            eid, existing_hash = cur.fetchone()
            if existing_hash != digest:
                raise IdempotencyConflictError(
                    f"artifact {key!r} already captured with different content for this attempt"
                )
            ids.append(eid)
    return ids


def get_artifacts(conn, execution_attempt_id: str) -> list[dict]:
    """Return the captured artifacts for an attempt (oldest first)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT artifact_key, artifact_type, artifact_ref, content_hash, size_bytes, metadata "
            "FROM execution_artifact WHERE execution_attempt_id = %s ORDER BY created_at, artifact_key",
            (execution_attempt_id,),
        )
        return [
            {"artifact_key": r[0], "artifact_type": r[1], "artifact_ref": r[2],
             "content_hash": r[3], "size_bytes": r[4], "metadata": r[5]}
            for r in cur.fetchall()
        ]
