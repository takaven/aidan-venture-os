"""Factory timeout classification (DB). A worker adapter's typed WorkerTimeoutError becomes
a canonical TIMEOUT attempt (retryable), never a generic WORKER_ERROR, and captures nothing.
"""
from __future__ import annotations

from aidan_core.errors import WorkerTimeoutError
from aidan_core.factory import runtime
from aidan_core.factory.verifiers import default_registry

from factory_fakes import FakeWorkerA, registry_with, spec_action


class _Raiser:
    kind = "raiser"

    def __init__(self, exc):
        self._exc = exc

    def execute(self, request):
        raise self._exc


def _dispatch(migrated, slug, exc):
    vid, aid, _ = spec_action(migrated, slug, worker_kind="raiser", verifier_kind="structured-contract",
                              expected_output_contract={"require": {"ok": True}})
    r = runtime.execute_action(migrated, aid, registry=registry_with(_Raiser(exc)))
    return aid, r


def test_worker_timeout_is_canonical_timeout(migrated):
    aid, r = _dispatch(migrated, "wt-timeout", WorkerTimeoutError("CODEX_TIMEOUT"))
    assert r.failure_class == "TIMEOUT"                      # not WORKER_ERROR
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 0                        # no timed-out result captured
        cur.execute("SELECT count(*) FROM execution_artifact WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 0                        # no artifacts captured


def test_ordinary_worker_exception_stays_worker_error(migrated):
    aid, r = _dispatch(migrated, "wt-err", ValueError("boom"))
    assert r.failure_class == "WORKER_ERROR"


def test_successful_worker_path_unchanged(migrated):
    vid, aid, _ = spec_action(migrated, "wt-ok", worker_kind="fake-a", verifier_kind="structured-contract",
                              expected_output_contract={"require": {"status": "done"}})
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
