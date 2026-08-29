"""Conservative capital accounting for the Codex provider path (DB; no bwrap, no real Codex).

Proves UNKNOWN COST != ZERO COST at the factory level: a failure/timeout AFTER the paid
provider was invoked HOLDS the reservation (RECOVERY_REQUIRED, not auto-claimable), while a
proven pre-invocation failure (provider never ran) fully releases it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from aidan_core import execution
from aidan_core.errors import ExecutionBlockedError, WorkerTimeoutError
from aidan_core.factory import runtime
from aidan_core.factory.codex_worker import CodexExecWorker, CodexProcessResult

from factory_fakes import registry_with, spec_action

CEIL = Decimal("1.0000")
TASK = {"prompt": "implement add(a,b)", "model": "gpt-5-mini", "artifact_paths": ["candidate.py"]}


class FakeTransport:
    def __init__(self, *, raise_exc=None):
        self.raise_exc = raise_exc
        self.calls = 0

    def __call__(self, argv, stdin_text, env, cwd, timeout):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return CodexProcessResult(0, '{"type":"turn.completed"}', "")


def _budget(migrated, vid):
    with migrated.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = 'USD'", (vid,))
        return cur.fetchone()


def _codex_action(migrated, slug):
    return spec_action(
        migrated, slug, worker_kind="codex-exec", verifier_kind="test-execution",
        amount=CEIL, grant=Decimal("5.0000"), max_attempts=1,
        capabilities=("WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"),
        task_payload=TASK, expected_output_contract={"test_execution": {"harness_source": "x"}})


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("WORKER_CODEX_API_KEY", "codex-test-not-real")


def test_post_invocation_timeout_holds_reservation(migrated, tmp_path, monkeypatch):
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: "/usr/bin/codex")
    (tmp_path / ".git").mkdir()
    vid, aid, _ = _codex_action(migrated, "cx-acct-timeout")
    worker = CodexExecWorker(transport=FakeTransport(raise_exc=WorkerTimeoutError("t")))
    r = runtime.execute_action(migrated, aid, registry=registry_with(worker), workspace_ref=str(tmp_path))
    assert r.failure_class == "AMBIGUOUS_EXTERNAL_EFFECT"
    assert execution.get_status(migrated, aid) == "RECOVERY_REQUIRED"
    reserved, committed = _budget(migrated, vid)
    assert reserved == CEIL and committed == Decimal("0.0000")   # HELD, not released to zero

    # And it is not auto-claimable -> no release-and-retry double-spend.
    with pytest.raises(ExecutionBlockedError):
        runtime.execute_action(migrated, aid, registry=registry_with(worker), workspace_ref=str(tmp_path))
    assert _budget(migrated, vid)[0] == CEIL


def test_pre_invocation_failure_releases_reservation(migrated, tmp_path, monkeypatch):
    # codex binary missing is checked BEFORE the transport -> provider never invoked -> safe release.
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: None)
    (tmp_path / ".git").mkdir()
    vid, aid, _ = _codex_action(migrated, "cx-acct-prefail")
    transport = FakeTransport()
    r = runtime.execute_action(migrated, aid, registry=registry_with(CodexExecWorker(transport=transport)),
                               workspace_ref=str(tmp_path))
    assert r.failure_class in ("WORKER_ERROR", "RETRY_EXHAUSTED")
    assert execution.get_status(migrated, aid) == "FAILED"
    assert transport.calls == 0                                   # provider never invoked
    reserved, committed = _budget(migrated, vid)
    assert reserved == Decimal("0.0000") and committed == Decimal("0.0000")   # released (no spend)
