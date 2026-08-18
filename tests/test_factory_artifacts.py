"""Gate 4 Slice 2 — durable, append-only, venture-consistent execution artifacts."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import execution, ventures
from aidan_core.errors import IdempotencyConflictError
from aidan_core.factory import artifacts as artifacts_mod, runtime

from factory_fakes import FakeWorkerA, registry_with, spec_action


def _decl(key="result", content="hello", ref="result.json", atype="STRUCTURED_RESULT"):
    return {"artifact_key": key, "artifact_type": atype, "ref": ref, "content": content}


def _claim_attempt(migrated, slug):
    """Dispatch a no-artifact worker to obtain a claimed attempt id."""
    vid, aid, _ = spec_action(migrated, slug)
    out = runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA()))
    return vid, aid, out.attempt_id


def test_artifact_captured_and_hashed_by_kernel(migrated):
    vid, aid, _ = spec_action(migrated, "art-cap")
    out = runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(artifacts=[_decl(content="hello")])))
    arts = artifacts_mod.get_artifacts(migrated, out.attempt_id)
    assert len(arts) == 1
    assert arts[0]["content_hash"] == artifacts_mod.content_hash("hello")  # kernel-computed
    # Artifact existence is NOT success.
    assert execution.get_status(migrated, aid) == "RUNNING"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 0


def test_worker_declared_hash_is_ignored(migrated):
    vid, aid, _ = spec_action(migrated, "art-declared")
    # Worker declares a bogus content_hash; the kernel hashes the real content instead.
    decl = _decl(content="real-content")
    decl["content_hash"] = "deadbeef-worker-declared"
    out = runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(artifacts=[decl])))
    stored = artifacts_mod.get_artifacts(migrated, out.attempt_id)[0]["content_hash"]
    assert stored == artifacts_mod.content_hash("real-content")
    assert stored != "deadbeef-worker-declared"


def test_artifact_is_append_only(migrated):
    vid, aid, attempt_id = _claim_attempt(migrated, "art-immutable")
    artifacts_mod.capture_artifacts(
        migrated, action_request_id=aid, execution_attempt_id=attempt_id, venture_id=vid,
        declarations=[_decl()],
    )
    for sql in (
        "UPDATE execution_artifact SET content_hash = 'x' WHERE execution_attempt_id = %s",
        "DELETE FROM execution_artifact WHERE execution_attempt_id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            with migrated.cursor() as cur:
                cur.execute(sql, (attempt_id,))


def test_artifact_idempotent_and_conflict(migrated):
    vid, aid, attempt_id = _claim_attempt(migrated, "art-idem")
    a = artifacts_mod.capture_artifacts(
        migrated, action_request_id=aid, execution_attempt_id=attempt_id, venture_id=vid, declarations=[_decl()])
    b = artifacts_mod.capture_artifacts(
        migrated, action_request_id=aid, execution_attempt_id=attempt_id, venture_id=vid, declarations=[_decl()])
    assert a == b  # same ids, converged
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_artifact WHERE execution_attempt_id = %s", (attempt_id,))
        assert cur.fetchone()[0] == 1
    with pytest.raises(IdempotencyConflictError):
        artifacts_mod.capture_artifacts(
            migrated, action_request_id=aid, execution_attempt_id=attempt_id, venture_id=vid,
            declarations=[_decl(content="DIFFERENT")])


def test_unsafe_artifact_ref_rejected(migrated):
    vid, aid, attempt_id = _claim_attempt(migrated, "art-unsafe")
    for bad in ("../escape", "a/../../b", "/abs/path", "C:\\win"):
        with pytest.raises(ValueError):
            artifacts_mod.capture_artifacts(
                migrated, action_request_id=aid, execution_attempt_id=attempt_id, venture_id=vid,
                declarations=[_decl(ref=bad)])


def test_cross_venture_artifact_rejected_by_db(migrated):
    vid, aid, attempt_id = _claim_attempt(migrated, "art-xv")
    vidB = ventures.create_venture(migrated, slug="art-xv-b")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO execution_artifact (venture_id, action_request_id, execution_attempt_id, "
                "artifact_key, artifact_type, artifact_ref, content_hash) "
                "VALUES (%s, %s, %s, 'k', 'OTHER', 'r', 'h')",
                (vidB, aid, attempt_id),
            )
