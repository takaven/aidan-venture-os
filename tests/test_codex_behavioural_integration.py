"""Codex adapter -> captured candidate -> trusted Bubblewrap TEST_EXECUTION -> Proof Receipt
(DB + bwrap). PRIMARY proof is BEHAVIOURAL, not pre-known bytes: correct code (any bytes)
verifies; wrong code is rejected even though the provider reports success. No real Codex.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aidan_core import execution
from aidan_core.factory import runtime, test_execution as te
from aidan_core.factory.codex_worker import CodexExecWorker, CodexProcessResult
from aidan_core.factory.verifiers import default_registry

from factory_fakes import registry_with, spec_action

pytestmark = pytest.mark.skipif(not te.bwrap_available(), reason="bwrap not available (non-Linux/dev)")

HARNESS = '''
import json, sys
sys.path.insert(0, "/candidate")
import candidate
total = passed = 0
for a, b in [(1, 2), (0, 0), (5, 7), (-3, 3)]:
    total += 1
    try:
        if candidate.add(a, b) == a + b:
            passed += 1
    except Exception:
        pass
print(json.dumps({"result": "PASS" if passed == total else "FAIL", "total": total, "passed": passed}))
'''

CONTRACT = {"test_execution": {
    "harness_source": HARNESS, "test_sha256": te._sha256_text(HARNESS), "min_tests": 4,
    "runner_kind": te.RUNNER_KIND, "runner_version": te.RUNNER_VERSION, "timeout_seconds": 25,
}}


class FakeCodexProc:
    """Simulates `codex exec`: writes the candidate into the workspace and emits a documented,
    successful JSONL event stream — regardless of whether the candidate is correct."""

    def __init__(self, src):
        self.src = src

    def __call__(self, argv, stdin_text, env, cwd, timeout):
        (Path(cwd) / "candidate.py").write_bytes(self.src.encode("utf-8"))
        stdout = (json.dumps({"type": "thread.started", "thread_id": "th_int"}) + "\n"
                  + json.dumps({"type": "turn.completed"}))
        return CodexProcessResult(0, stdout, "")


def _drive(migrated, tmp_path, slug, src, monkeypatch):
    monkeypatch.setenv("WORKER_OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: "/usr/bin/codex")
    vid, aid, _ = spec_action(
        migrated, slug, worker_kind="codex-exec", verifier_kind="test-execution",
        capabilities=("WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"),
        task_payload={"prompt": "implement add(a,b)", "model": "gpt-5-mini",
                      "artifact_paths": ["candidate.py"]},
        expected_output_contract=CONTRACT)
    runtime.execute_action(migrated, aid, registry=registry_with(CodexExecWorker(transport=FakeCodexProc(src))),
                           workspace_ref=str(tmp_path))
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    return aid, out


def test_A_correct_candidate_verifies(migrated, tmp_path, monkeypatch):
    aid, out = _drive(migrated, tmp_path, "cx-A", "def add(a, b):\n    return a + b\n", monkeypatch)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert execution.get_status(migrated, aid) == "SUCCEEDED"


def test_A2_different_bytes_same_behaviour_verifies(migrated, tmp_path, monkeypatch):
    aid, out = _drive(migrated, tmp_path, "cx-A2",
                      "def add(a, b):\n    # different exact bytes\n    return b + a\n", monkeypatch)
    assert out.status == "SUCCEEDED" and out.verified is True


def test_B_wrong_candidate_rejected_despite_provider_success(migrated, tmp_path, monkeypatch):
    # The fake provider emits a SUCCESS event stream; the code is wrong -> the behavioural
    # verifier still REJECTS. Provider/worker self-report is not verification authority.
    aid, out = _drive(migrated, tmp_path, "cx-B", "def add(a, b):\n    return a * b\n", monkeypatch)
    assert out.status != "SUCCEEDED"
    assert execution.get_status(migrated, aid) != "SUCCEEDED"


def test_captured_candidate_hash_equals_executed_and_evidence_durable(migrated, tmp_path, monkeypatch):
    aid, out = _drive(migrated, tmp_path, "cx-bind", "def add(a, b):\n    return a + b\n", monkeypatch)
    with migrated.cursor() as cur:
        cur.execute("SELECT raw_payload FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC LIMIT 1", (aid,))
        raw = cur.fetchone()[0]
        cur.execute("SELECT payload FROM audit_event WHERE action_id = %s "
                    "AND event_type = 'factory.test_execution_evidence' ORDER BY occurred_at DESC LIMIT 1",
                    (aid,))
        evrow = cur.fetchone()
    candidate_files = {a["artifact_key"]: a["content"] for a in raw.get("artifacts", [])
                       if isinstance(a.get("content"), str)}
    assert candidate_files == {"candidate.py": "def add(a, b):\n    return a + b\n"}
    ev = evrow[0]["evidence"]
    assert ev["candidate_sha256"] == te.canonical_candidate_hash(candidate_files)   # executed == captured
    assert evrow[0]["verdict"] == "VERIFIED"
