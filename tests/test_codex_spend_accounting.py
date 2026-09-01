"""Corrected capital accounting for the Codex provider path (DB; no bwrap, no real Codex).

Separates EXECUTION-outcome certainty from COST certainty. A post-invocation Codex
failure/timeout keeps its TRUE FAILED/TIMEOUT class and conservatively reconciles possibly-
incurred cost against the frozen action ceiling (kernel estimate when trusted usage exists,
else the full ceiling) — never released as zero, never RECOVERY_REQUIRED for mere cost
uncertainty, and terminal (no paid retry). A proven pre-invocation failure fully releases.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from aidan_core import execution
from aidan_core.errors import ExecutionBlockedError, WorkerTimeoutError
from aidan_core.factory import runtime
from aidan_core.factory.codex_worker import CodexExecWorker, CodexProcessResult

from factory_fakes import registry_with, spec_action

CEIL = Decimal("1.0000")
TASK = {"prompt": "implement add(a,b)", "model": "gpt-5-mini", "artifact_paths": ["candidate.py"]}
USAGE = {"input_tokens": 20_000, "output_tokens": 5_000}     # 0.005 + 0.010 = 0.015 at gpt-5-mini
EST = Decimal("0.0150")


class FakeTransport:
    def __init__(self, *, raise_exc=None, events=None, writes=None, exit_code=0):
        self.raise_exc = raise_exc
        self.events = events if events is not None else [{"type": "turn.completed"}]
        self.writes = writes or {}
        self.exit_code = exit_code
        self.calls = 0

    def __call__(self, argv, stdin_text, env, cwd, timeout):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        from pathlib import Path
        for rel, c in self.writes.items():
            (Path(cwd) / rel).write_bytes(c.encode("utf-8"))
        return CodexProcessResult(self.exit_code, "\n".join(json.dumps(e) for e in self.events), "")


def _budget(migrated, vid):
    with migrated.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = 'USD'", (vid,))
        return cur.fetchone()


def _codex_action(migrated, slug, *, grant=Decimal("5.0000"), max_attempts=1):
    return spec_action(
        migrated, slug, worker_kind="codex-exec", verifier_kind="test-execution",
        amount=CEIL, grant=grant, max_attempts=max_attempts,
        capabilities=("WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"),
        task_payload=TASK, expected_output_contract={"test_execution": {"harness_source": "x"}})


@pytest.fixture(autouse=True)
def _codex(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKER_CODEX_API_KEY", "codex-test-not-real")
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: "/usr/bin/codex")
    (tmp_path / ".git").mkdir()


def _dispatch(migrated, tmp_path, aid, transport):
    return runtime.execute_action(migrated, aid, registry=registry_with(CodexExecWorker(transport=transport)),
                                  workspace_ref=str(tmp_path))


# (1) pre-invocation failure -> full release
def test_pre_invocation_failure_releases(migrated, tmp_path, monkeypatch):
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: None)   # provider never invoked
    vid, aid, _ = _codex_action(migrated, "cx-pre")
    t = FakeTransport()
    r = _dispatch(migrated, tmp_path, aid, t)
    assert r.failure_class in ("WORKER_ERROR", "RETRY_EXHAUSTED")
    assert execution.get_status(migrated, aid) == "FAILED"
    assert t.calls == 0 and _budget(migrated, vid) == (Decimal("0.0000"), Decimal("0.0000"))


# (2) known failure + trusted usage -> commit derived estimate, preserve FAILED
def test_known_failure_with_usage_commits_estimate(migrated, tmp_path):
    vid, aid, _ = _codex_action(migrated, "cx-knownfail")
    t = FakeTransport(events=[{"type": "turn.failed", "usage": USAGE}])
    r = _dispatch(migrated, tmp_path, aid, t)
    assert r.failure_class == "WORKER_ERROR"
    assert execution.get_status(migrated, aid) == "FAILED"     # true outcome, NOT RECOVERY_REQUIRED
    assert _budget(migrated, vid) == (Decimal("0.0000"), EST)  # estimate committed, remainder released


# (3) known failure + NO trusted usage -> commit full ceiling (never zero)
def test_unknown_cost_failure_commits_full_ceiling(migrated, tmp_path):
    vid, aid, _ = _codex_action(migrated, "cx-unknown")
    t = FakeTransport(events=[{"type": "turn.failed"}])         # no usage
    r = _dispatch(migrated, tmp_path, aid, t)
    assert r.failure_class == "WORKER_ERROR"
    assert execution.get_status(migrated, aid) == "FAILED"
    assert _budget(migrated, vid) == (Decimal("0.0000"), CEIL)  # conservative full ceiling committed


# (4) timeout after invocation -> TIMEOUT class + conservative ceiling, not zero
def test_timeout_commits_ceiling_not_zero(migrated, tmp_path):
    vid, aid, _ = _codex_action(migrated, "cx-timeout")
    t = FakeTransport(raise_exc=WorkerTimeoutError("t"))
    r = _dispatch(migrated, tmp_path, aid, t)
    assert r.failure_class == "TIMEOUT"
    assert execution.get_status(migrated, aid) == "FAILED"
    assert _budget(migrated, vid) == (Decimal("0.0000"), CEIL)


# (5) committed cost never exceeds the frozen ceiling
def test_committed_never_exceeds_ceiling(migrated, tmp_path):
    vid, aid, _ = _codex_action(migrated, "cx-cap")
    huge = {"input_tokens": 10**9, "output_tokens": 10**9}
    r = _dispatch(migrated, tmp_path, aid, FakeTransport(events=[{"type": "turn.failed", "usage": huge}]))
    assert r.failure_class == "WORKER_ERROR"
    assert _budget(migrated, vid) == (Decimal("0.0000"), CEIL)   # capped at ceiling


# (6) no paid retry after a terminal post-invocation failure (even with attempts remaining)
def test_no_paid_retry_after_post_invocation_failure(migrated, tmp_path):
    vid, aid, _ = _codex_action(migrated, "cx-noretry", max_attempts=3)
    t = FakeTransport(events=[{"type": "turn.failed", "usage": USAGE}])
    _dispatch(migrated, tmp_path, aid, t)
    assert execution.get_status(migrated, aid) == "FAILED"       # terminal despite attempts remaining
    with pytest.raises(ExecutionBlockedError):
        _dispatch(migrated, tmp_path, aid, t)
    assert t.calls == 1                                          # provider invoked exactly once


# (7) capital is isolated per venture
def test_capital_isolated_per_venture(migrated, tmp_path):
    vidA, aidA, _ = _codex_action(migrated, "cx-A")
    vidB, aidB, _ = _codex_action(migrated, "cx-B")
    _dispatch(migrated, tmp_path, aidA, FakeTransport(events=[{"type": "turn.failed"}]))
    assert _budget(migrated, vidB) == (Decimal("0.0000"), Decimal("0.0000"))
    assert _budget(migrated, vidA) == (Decimal("0.0000"), CEIL)


# (8) the cost-bearing-failure audit event persists ONLY bounded, safe machine fields, so a failed
# live run stays diagnosable after the ephemeral DB is gone — no raw stderr/transcript/secret.
def test_cost_bearing_failure_audit_event_carries_safe_fields(migrated, tmp_path):
    vid, aid, _ = _codex_action(migrated, "cx-audit")
    # exit 4 with no terminal/thread event: the smoke-#2 shape.
    _dispatch(migrated, tmp_path, aid, FakeTransport(exit_code=4, events=[]))
    with migrated.cursor() as cur:
        cur.execute("SELECT payload FROM audit_event WHERE action_id = %s "
                    "AND event_type = 'factory.provider_cost_bearing_failure' "
                    "ORDER BY occurred_at DESC LIMIT 1", (aid,))
        payload = cur.fetchone()[0]
    assert payload["code"] == "CODEX_NONZERO_EXIT"
    assert payload["failure_class"] == "WORKER_ERROR"
    assert payload["provider_contact"] == "UNKNOWN"     # boundary crossed != provider request proven
    assert payload["process_exit_code"] == 4
    assert payload["usage_observed"] is False
    assert payload["committed_cost"] == "1.0000"        # conservative full ceiling
    # No unbounded/raw fields leaked into the audit payload.
    assert set(payload) == {"attempt_id", "failure_class", "code", "committed_cost",
                            "provider_contact", "process_exit_code", "usage_observed"}
