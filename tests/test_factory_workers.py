"""Gate 4 Slice 1 — typed, provider-neutral, DB-authority-free worker boundary.

Pure (no database) tests: they exercise the contract shapes and registry only.
"""
from __future__ import annotations

import inspect

import pytest

from aidan_core.factory.workers import WorkerAdapter, WorkerRegistry, WorkerRequest

from factory_fakes import FakeWorkerA, FakeWorkerB


def _request(kind="fake-a"):
    return WorkerRequest(
        action_request_id="a", attempt_id="t", venture_id="v", spec_hash="h", worker_kind=kind,
        task_payload={"goal": "x"}, declared_inputs={}, capabilities=("READ_REPOSITORY",),
        timeout_seconds=60, workspace_ref="mock://ws", expected_output_contract={},
    )


def test_registry_dispatch_by_kind():
    reg = WorkerRegistry()
    a, b = FakeWorkerA(), FakeWorkerB()
    reg.register(a)
    reg.register(b)
    assert reg.get("fake-a") is a and reg.get("fake-b") is b
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_fakes_satisfy_the_adapter_protocol():
    assert isinstance(FakeWorkerA(), WorkerAdapter)
    assert isinstance(FakeWorkerB(), WorkerAdapter)


def test_worker_receives_no_db_connection():
    # The request carries only bounded execution data — no connection/credential.
    req = _request()
    for forbidden in ("conn", "connection", "cursor", "db", "dsn", "credential", "secret"):
        assert not hasattr(req, forbidden)
    # execute takes only the request (besides self): no place to pass a connection.
    params = list(inspect.signature(FakeWorkerA().execute).parameters)
    assert params == ["request"]


def test_worker_result_is_a_claim_not_a_verdict():
    res = FakeWorkerA().execute(_request())
    # A claim field, not a canonical success verdict.
    assert hasattr(res, "reported_outcome")
    assert not hasattr(res, "verified") and not hasattr(res, "proof")
    # No canonical status enum leaks into the worker result.
    assert res.reported_outcome == "success"  # a string claim only
    assert isinstance(res.external_result_id, str) and res.external_result_id


def test_provider_identity_is_only_provenance():
    # worker_kind/version are provenance strings; no provider name is structural.
    res = FakeWorkerB().execute(_request(kind="fake-b"))
    assert res.worker_kind == "fake-b" and res.worker_version == "test"
