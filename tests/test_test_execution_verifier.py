"""Pure TestExecutionVerifier tests — no sandbox, no DB (run everywhere).

Proves the verifier derives VERIFIED only from trusted machine-owned evidence bound to
the frozen test + trusted runner + captured candidate, and that worker self-report can
never confer success. Sandbox EXECUTION behaviour is proven separately (Linux+bwrap).
"""
from __future__ import annotations

from aidan_core.factory.verifiers import (
    TEST_EXECUTION, TestExecutionVerifier, VerificationRequest, default_registry)

TS = "a" * 64          # frozen harness sha256
CH = "b" * 64          # captured candidate sha256


def _contract(min_tests=2):
    return {"test_execution": {"test_sha256": TS, "min_tests": min_tests,
                               "runner_kind": "bwrap-python-stdlib", "runner_version": "1"}}


def _evidence(**over):
    ev = dict(runner_kind="bwrap-python-stdlib", runner_version="1", bwrap_version="bubblewrap 0.9.0",
              terminal_state="COMPLETED", exit_code=0, harness_result="PASS",
              tests_total=3, tests_passed=3, candidate_sha256=CH, test_sha256=TS,
              timeout_seconds=30, detail="")
    ev.update(over)
    return ev


def _req(evidence, *, candidate_hash=CH, contract=None, worker=None, artifacts=()):
    return VerificationRequest(
        action_request_id="a", execution_attempt_id="att-1", verifier_kind="test-execution",
        expected_output_contract=contract or _contract(),
        worker_structured_output=worker or {}, artifacts=artifacts, spec_hash="spec-1",
        test_evidence=evidence, candidate_content_hash=candidate_hash)


V = TestExecutionVerifier()


def test_registry_has_test_execution():
    assert default_registry().get("test-execution").verification_type == TEST_EXECUTION


def test_valid_trusted_evidence_verifies():
    assert V.verify(_req(_evidence())).verdict == "VERIFIED"


def test_missing_evidence_rejected():
    r = V.verify(_req(None))
    assert r.verdict == "REJECTED" and "no_evidence" in r.detail["reasons"]


def test_worker_self_report_cannot_confer_success():
    # Worker screams success every way it can; no trusted evidence -> REJECTED.
    r = V.verify(_req(None, worker={"tests_pass": True, "status": "SUCCEEDED", "result": "PASS"},
                      artifacts=({"artifact_key": "TEST_REPORT", "artifact_type": "TEST_REPORT",
                                  "content": '{"result":"PASS","total":9,"passed":9}'},)))
    assert r.verdict == "REJECTED"


def test_worker_fake_test_report_artifact_is_ignored():
    # A forged TEST_REPORT artifact + a genuinely FAILED sandbox run -> REJECTED.
    r = V.verify(_req(_evidence(harness_result="FAIL", tests_passed=0),
                      artifacts=({"artifact_key": "TEST_REPORT", "content": '{"result":"PASS"}'},)))
    assert r.verdict == "REJECTED" and "harness_result" in r.detail["reasons"]


def test_wrong_candidate_binding_rejected():
    r = V.verify(_req(_evidence(candidate_sha256="c" * 64)))   # evidence for a different candidate
    assert r.verdict == "REJECTED" and "candidate_sha256" in r.detail["reasons"]


def test_changed_frozen_test_identity_rejected():
    r = V.verify(_req(_evidence(test_sha256="d" * 64)))        # not the frozen harness
    assert r.verdict == "REJECTED" and "test_sha256" in r.detail["reasons"]


def test_runner_identity_must_match_contract():
    assert "runner_kind" in V.verify(_req(_evidence(runner_kind="evil-runner"))).detail["reasons"]
    assert "runner_version" in V.verify(_req(_evidence(runner_version="999"))).detail["reasons"]


def test_nonzero_exit_rejected():
    assert V.verify(_req(_evidence(exit_code=1))).verdict == "REJECTED"


def test_timeout_rejected():
    r = V.verify(_req(_evidence(terminal_state="TIMEOUT", exit_code=None, harness_result="NONE",
                                tests_total=None, tests_passed=None)))
    assert r.verdict == "REJECTED" and "terminal_state" in r.detail["reasons"]


def test_harness_fail_rejected():
    assert V.verify(_req(_evidence(harness_result="FAIL", tests_passed=1))).verdict == "REJECTED"


def test_insufficient_or_partial_tests_rejected():
    assert "test_counts" in V.verify(_req(_evidence(tests_total=1, tests_passed=1))).detail["reasons"]  # < min
    assert "test_counts" in V.verify(_req(_evidence(tests_total=3, tests_passed=2))).detail["reasons"]  # not all passed


def test_evidence_hash_binds_attempt_and_spec():
    # Same evidence bound to a different attempt/spec yields a different evidence hash,
    # so a receipt cannot be transplanted across attempts/specs.
    base = V.verify(_req(_evidence()))
    other = VerificationRequest(
        action_request_id="a", execution_attempt_id="att-2", verifier_kind="test-execution",
        expected_output_contract=_contract(), worker_structured_output={}, artifacts=(),
        spec_hash="spec-2", test_evidence=_evidence(), candidate_content_hash=CH)
    assert V.verify(other).evidence_hash != base.evidence_hash


def test_verify_runs_no_subprocess(monkeypatch):
    # The pure verifier must never execute anything; make subprocess explode and verify still works.
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess!")))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess!")))
    assert V.verify(_req(_evidence())).verdict == "VERIFIED"
