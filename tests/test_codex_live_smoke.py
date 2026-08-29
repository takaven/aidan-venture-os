"""Deterministic proof of the frozen live-Codex-smoke entrypoint — NO real Codex.

Proves frozen-hash fail-closed guards, capital preconditions, at-most-one provider invocation,
and the full behavioural + conservative-cost path via an injected fake provider.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from aidan_core.factory import codex_live_smoke as cls
from aidan_core.factory import test_execution as te
from aidan_core.factory.codex_live_smoke import FrozenMismatch
from aidan_core.factory.codex_worker import CodexProcessResult

CORRECT = "import re\ndef slugify(text):\n    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n"
WRONG = "def slugify(text):\n    return text.lower().replace(' ', '-')\n"


class FakeCodex:
    def __init__(self, *, src=None, events=None, exit_code=0, raise_exc=None):
        self.src, self.exit_code, self.raise_exc = src, exit_code, raise_exc
        self.events = events if events is not None else [{"type": "turn.completed"}]

    def __call__(self, argv, stdin_text, env, cwd, timeout):
        if self.raise_exc:
            raise self.raise_exc
        if self.src is not None:
            (Path(cwd) / "candidate.py").write_bytes(self.src.encode("utf-8"))
        return CodexProcessResult(self.exit_code, "\n".join(json.dumps(e) for e in self.events), "")


# ---- A/B/C/G/F: frozen fail-closed guards (no dispatch) ----------------------

def test_A_frozen_spec_and_harness_match():
    task, contract, caps = cls.frozen_spec_inputs()          # raises if drift
    assert task["model"] == "gpt-5-mini" and contract["test_execution"]["test_sha256"] == cls.FROZEN_HARNESS_SHA256


def test_B_wrong_harness_bytes_fail_before_dispatch(monkeypatch):
    monkeypatch.setattr(cls, "_load_harness", lambda: "print('tampered')\n")
    with pytest.raises(FrozenMismatch):
        cls.frozen_spec_inputs()


def test_C_wrong_model_spec_hash_mismatch(monkeypatch):
    monkeypatch.setattr(cls, "MODEL", "gpt-5")               # changes the spec hash
    with pytest.raises(FrozenMismatch):
        cls.frozen_spec_inputs()


def test_G_wrong_codex_version_fails():
    cls.assert_codex_version("codex-cli 0.151.0")            # frozen -> ok
    with pytest.raises(FrozenMismatch):
        cls.assert_codex_version("codex-cli 0.150.0")


def test_F_wrong_confirm_token_blocks(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("CONFIRM", "nope")
    assert cls.main() == 2
    assert "CONFIRM_REQUIRED" in capsys.readouterr().out


# ---- D/E/J/K: DB accounting + invocation bound (no bwrap) --------------------

@pytest.fixture
def _codex(monkeypatch):
    monkeypatch.setenv("WORKER_CODEX_API_KEY", "codex-test-not-real")
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: "/usr/bin/codex")


def test_D_reservation_failure_zero_invocations(migrated, _codex):
    ev = cls.run_smoke(migrated, transport=FakeCodex(src=CORRECT), grant=Decimal("0.10"), slug="cx-smoke-D")
    assert ev["result"] == "RESERVATION_FAILED" and ev["provider_invocations"] == 0


def test_E_missing_credential_zero_invocations(migrated, monkeypatch):
    monkeypatch.delenv("WORKER_CODEX_API_KEY", raising=False)
    monkeypatch.setattr("aidan_core.factory.codex_worker.codex_bin", lambda: "/usr/bin/codex")
    ev = cls.run_smoke(migrated, transport=FakeCodex(src=CORRECT), slug="cx-smoke-E")
    assert ev["provider_invocations"] == 0 and ev["result"] == "FAIL"


def test_J_post_invocation_failure_no_usage_conservative(migrated, _codex):
    ev = cls.run_smoke(migrated, transport=FakeCodex(events=[{"type": "turn.failed"}]), slug="cx-smoke-J")
    assert ev["provider_invocations"] == 1 and ev["result"] == "FAIL"
    assert ev["committed"] == "0.2000" and ev["final_status"] == "FAILED"   # full ceiling, not zero


def test_K_known_failure_with_usage_commits_estimate(migrated, _codex):
    usage = {"input_tokens": 20000, "output_tokens": 5000}   # 0.015 at gpt-5-mini
    ev = cls.run_smoke(migrated, transport=FakeCodex(events=[{"type": "turn.failed", "usage": usage}]),
                       slug="cx-smoke-K")
    assert ev["provider_invocations"] == 1 and ev["committed"] == "0.0150"


# ---- H/I: full behavioural path (DB + bwrap) --------------------------------

@pytest.mark.skipif(not te.bwrap_available(), reason="bwrap not available (non-Linux/dev)")
def test_H_correct_candidate_verifies_and_reconciles(migrated, _codex):
    usage = {"input_tokens": 25000, "output_tokens": 8000}
    ev = cls.run_smoke(migrated, transport=FakeCodex(
        src=CORRECT, events=[{"type": "thread.started", "thread_id": "th"},
                             {"type": "turn.completed", "usage": usage}]), slug="cx-smoke-H")
    assert ev["provider_invocations"] == 1
    assert ev["result"] == "PASS" and ev["test_execution_verdict"] == "VERIFIED"
    assert ev["proof_result"] == "VERIFIED" and ev["governance_deltas"] == 0
    assert ev["secret_leak_check"] == "PASS"
    assert Decimal(ev["committed"]) <= cls.CEILING and Decimal(ev["reserved"]) == Decimal("0.0000")


@pytest.mark.skipif(not te.bwrap_available(), reason="bwrap not available (non-Linux/dev)")
def test_I_wrong_candidate_rejected(migrated, _codex):
    ev = cls.run_smoke(migrated, transport=FakeCodex(
        src=WRONG, events=[{"type": "thread.started", "thread_id": "th"},
                          {"type": "turn.completed"}]), slug="cx-smoke-I")
    assert ev["provider_invocations"] == 1
    assert ev["result"] == "FAIL" and ev["test_execution_verdict"] == "REJECTED"
    assert ev["final_status"] != "SUCCEEDED"
