"""Bubblewrap sandbox runner behaviour + security (Linux + bwrap only).

Proves the trusted runner executes a FROZEN behavioural harness against candidate code
and produces machine-owned evidence: correct code passes (regardless of exact bytes),
wrong/faulty code fails, timeouts are contained, and untrusted candidates cannot reach
the network, the canonical repo, or host secrets. Skipped where bwrap is unavailable
(non-Linux dev); enforced in CI.
"""
from __future__ import annotations

import json
import os

import pytest

from aidan_core.factory import test_execution as te
from aidan_core.factory.verifiers import TestExecutionVerifier, VerificationRequest

pytestmark = pytest.mark.skipif(not te.bwrap_available(), reason="bwrap not available (non-Linux/dev)")

# A FROZEN behavioural harness — tests add(a,b) by RESULT, never by source bytes.
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

A_CORRECT = {"candidate.py": "def add(a, b):\n    return a + b\n"}
A_CORRECT2 = {"candidate.py": "def add(a, b):\n    # different bytes, same behaviour\n    return b + a\n"}
B_WRONG = {"candidate.py": "def add(a, b):\n    return a * b\n"}
SYNTAX_ERR = {"candidate.py": "def add(a, b) return a + b\n"}
RUNTIME_ERR = {"candidate.py": "def add(a, b):\n    raise ValueError('boom')\n"}
SLEEPER = {"candidate.py": "import time\ntime.sleep(60)\ndef add(a, b):\n    return a + b\n"}


def _run(candidate, harness=HARNESS, timeout=25):
    return te.run_frozen_tests(candidate_files=candidate, harness_source=harness, timeout_seconds=timeout)


def test_correct_candidate_passes():
    ev = _run(A_CORRECT)
    assert ev.terminal_state == te.COMPLETED and ev.exit_code == 0
    assert ev.harness_result == "PASS" and ev.tests_total == 4 and ev.tests_passed == 4


def test_different_bytes_but_correct_also_passes():
    a = te.canonical_candidate_hash(A_CORRECT)
    a2 = te.canonical_candidate_hash(A_CORRECT2)
    assert a != a2                                   # exact bytes were NOT pre-known
    assert _run(A_CORRECT2).harness_result == "PASS"


def test_wrong_candidate_fails():
    ev = _run(B_WRONG)
    assert ev.terminal_state == te.COMPLETED and ev.harness_result == "FAIL" and ev.tests_passed < ev.tests_total


def test_syntax_error_fails():
    ev = _run(SYNTAX_ERR)
    assert ev.harness_result != "PASS" and ev.exit_code not in (0, None)


def test_runtime_exception_fails():
    assert _run(RUNTIME_ERR).harness_result == "FAIL"


def test_timeout_is_contained():
    ev = _run(SLEEPER, timeout=3)
    assert ev.terminal_state == te.TIMEOUT and ev.harness_result == "NONE"


def test_full_pipeline_correct_candidate_verifies():
    ev = _run(A_CORRECT)
    contract = {"test_execution": {"test_sha256": te._sha256_text(HARNESS), "min_tests": 4,
                                   "runner_kind": te.RUNNER_KIND, "runner_version": te.RUNNER_VERSION}}
    req = VerificationRequest(
        action_request_id="a", execution_attempt_id="1", verifier_kind="test-execution",
        expected_output_contract=contract, worker_structured_output={}, artifacts=(), spec_hash="s",
        test_evidence=ev.to_dict(), candidate_content_hash=te.canonical_candidate_hash(A_CORRECT))
    assert TestExecutionVerifier().verify(req).verdict == "VERIFIED"


def test_wrong_candidate_never_verifies_through_pipeline():
    ev = _run(B_WRONG)
    contract = {"test_execution": {"test_sha256": te._sha256_text(HARNESS), "min_tests": 4,
                                   "runner_kind": te.RUNNER_KIND, "runner_version": te.RUNNER_VERSION}}
    req = VerificationRequest(
        action_request_id="a", execution_attempt_id="1", verifier_kind="test-execution",
        expected_output_contract=contract, worker_structured_output={}, artifacts=(), spec_hash="s",
        test_evidence=ev.to_dict(), candidate_content_hash=te.canonical_candidate_hash(B_WRONG))
    assert TestExecutionVerifier().verify(req).verdict == "REJECTED"


# ---- security: an untrusted candidate cannot escape the sandbox --------------

_NET = '''
import json, socket
contained = False
try:
    s = socket.socket(); s.settimeout(3); s.connect(("1.1.1.1", 53)); s.close()
except Exception:
    contained = True
print(json.dumps({"result": "PASS" if contained else "FAIL", "total": 1, "passed": 1 if contained else 0}))
'''

_SECRET = '''
import json, os
contained = os.environ.get("AIDAN_TEST_SECRET") is None and os.environ.get("HOME") == "/work"
print(json.dumps({"result": "PASS" if contained else "FAIL", "total": 1, "passed": 1 if contained else 0}))
'''


def test_candidate_cannot_use_network():
    assert _run({"candidate.py": ""}, harness=_NET).harness_result == "PASS"


def test_candidate_cannot_read_host_secret_or_home(monkeypatch):
    monkeypatch.setenv("AIDAN_TEST_SECRET", "should-not-leak")
    assert _run({"candidate.py": ""}, harness=_SECRET).harness_result == "PASS"


def test_candidate_cannot_write_canonical_repo():
    repo = os.getcwd()   # the checkout is NOT mounted into the sandbox
    harness = (
        'import json, os\n'
        'contained = False\n'
        'try:\n'
        f'    open(os.path.join({repo!r}, "ESCAPE_SENTINEL"), "w").write("x")\n'
        'except Exception:\n'
        '    contained = True\n'
        'print(json.dumps({"result": "PASS" if contained else "FAIL", "total": 1, "passed": 1 if contained else 0}))\n'
    )
    ev = _run({"candidate.py": ""}, harness=harness)
    assert ev.harness_result == "PASS"
    assert not os.path.exists(os.path.join(repo, "ESCAPE_SENTINEL"))
