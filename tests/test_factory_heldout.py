"""Gate 4 — Track A HELD-OUT machine-execution evals.

Kept separate from the development matrix, with distinct scenario values, driving
the SAME production Factory runtime with no production special-casing and no
injected expected outcomes. These prove the Gate 4 execution invariants generalize
beyond the development fixtures — machine execution only, not commercial success.
"""
from __future__ import annotations

import pytest

from aidan_core import execution, killswitch
from aidan_core.errors import ExecutionBlockedError
from aidan_core.factory import artifacts as artifacts_mod, runtime

from factory_fakes import FakeWorkerA, FakeWorkerB, FlakyWorker, registry_with, spec_action

_OK = {"require": {"phase": "complete"}}


def _sc(migrated, slug, **kw):
    kw.setdefault("verifier_kind", "structured-contract")
    kw.setdefault("expected_output_contract", _OK)
    return spec_action(migrated, slug, **kw)


def _verified(migrated, aid):
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'", (aid,))
        return cur.fetchone()[0]


def test_H1_alternate_worker_adapter_success(migrated):
    vid, aid, sp = _sc(migrated, "h1", worker_kind="fake-b")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerB(structured_output={"phase": "complete"})))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True


def test_H2_retry_after_worker_failure(migrated):
    vid, aid, sp = _sc(migrated, "h2", max_attempts=2)
    reg = registry_with(FlakyWorker(fail_first=1, structured_output={"phase": "complete"}))
    assert runtime.execute_action(migrated, aid, registry=reg).failure_class == "WORKER_ERROR"
    runtime.execute_action(migrated, aid, registry=reg)
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 2  # both attempts durable


def test_H3_wrong_artifact_no_success(migrated):
    content = "held-out-artifact"
    vid, aid, sp = spec_action(
        migrated, "h3", verifier_kind="artifact-hash", max_attempts=1,
        expected_output_contract={"artifact_key": "r", "expected_sha256": artifacts_mod.content_hash(content)})
    worker = FakeWorkerA(artifacts=[{"artifact_key": "r", "artifact_type": "PATCH", "ref": "r.diff", "content": "TAMPERED"}])
    runtime.execute_action(migrated, aid, registry=registry_with(worker))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status != "SUCCEEDED" and _verified(migrated, aid) == 0


def test_H4_exact_funded_retry(migrated):
    vid, aid, sp = _sc(migrated, "h4", amount=25, grant=25, max_attempts=2)
    reg = registry_with(FlakyWorker(fail_first=1, structured_output={"phase": "complete"}))
    assert runtime.execute_action(migrated, aid, registry=reg).failure_class == "WORKER_ERROR"
    assert runtime.execute_action(migrated, aid, registry=reg).dispatched is True
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM capital_entry WHERE action_request_id = %s AND entry_type = 'RESERVE'", (aid,))
        assert cur.fetchone()[0] == 1
    assert runtime.verify_and_complete(migrated, aid, actual_cost=25).status == "SUCCEEDED"


def test_H5_kill_after_result_blocks_completion(migrated):
    vid, aid, sp = _sc(migrated, "h5")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"phase": "complete"})))
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert _verified(migrated, aid) == 0 and execution.get_status(migrated, aid) != "SUCCEEDED"


def test_H6_durable_result_restart(migrated):
    vid, aid, sp = _sc(migrated, "h6")
    worker = FakeWorkerA(structured_output={"phase": "complete"})
    runtime.execute_action(migrated, aid, registry=registry_with(worker))
    assert worker.calls == 1
    res = runtime.resume_action(migrated, aid, actual_cost=10)  # fresh: no worker
    assert res["status"] == "SUCCEEDED" and worker.calls == 1


def test_H7_idempotent_recovery_executes(migrated):
    vid, aid, sp = _sc(migrated, "h7", max_attempts=2)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT", lease_seconds=-1)
    worker = FakeWorkerA(structured_output={"phase": "complete"})
    res = runtime.resume_action(migrated, aid, registry=registry_with(worker), actual_cost=10)
    assert res["outcome"] == "recovered_and_dispatched" and worker.calls == 1
    assert runtime.verify_and_complete(migrated, aid, actual_cost=10).status == "SUCCEEDED"
