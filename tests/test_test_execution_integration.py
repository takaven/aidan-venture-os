"""End-to-end: TEST_EXECUTION spec -> worker candidate -> trusted sandbox evidence ->
pure verifier -> canonical Proof Receipt (DB + bwrap). Proves canonical SUCCESS follows
behavioural correctness, never worker self-report. Skipped without bwrap.
"""
from __future__ import annotations

import pytest

from aidan_core import execution
from aidan_core.factory import runtime, test_execution as te
from aidan_core.factory.verifiers import default_registry

from factory_fakes import FakeWorkerA, registry_with, spec_action

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


def _drive(migrated, slug, *, candidate_src, worker_outcome="success", structured=None):
    vid, aid, _ = spec_action(migrated, slug, verifier_kind="test-execution", expected_output_contract=CONTRACT)
    decl = {"artifact_key": "candidate.py", "artifact_type": "FILE", "ref": "candidate.py", "content": candidate_src}
    runtime.execute_action(migrated, aid, registry=registry_with(
        FakeWorkerA(reported_outcome=worker_outcome, structured_output=structured or {}, artifacts=[decl])))
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    return aid, out


def test_correct_candidate_reaches_canonical_success(migrated):
    aid, out = _drive(migrated, "tx-ok", candidate_src="def add(a, b):\n    return a + b\n")
    assert out.status == "SUCCEEDED" and out.verified is True
    assert execution.get_status(migrated, aid) == "SUCCEEDED"


def test_different_bytes_correct_candidate_also_succeeds(migrated):
    aid, out = _drive(migrated, "tx-ok2", candidate_src="def add(a, b):\n    return b + a  # different bytes\n")
    assert out.status == "SUCCEEDED" and out.verified is True


def test_wrong_candidate_never_succeeds(migrated):
    aid, out = _drive(migrated, "tx-wrong", candidate_src="def add(a, b):\n    return a * b\n")
    assert out.status != "SUCCEEDED"
    assert execution.get_status(migrated, aid) != "SUCCEEDED"


def test_worker_self_report_cannot_buy_success_for_wrong_code(migrated):
    # Worker lies maximally: reported_outcome=success + tests_pass=true, but code is WRONG.
    aid, out = _drive(migrated, "tx-liar", candidate_src="def add(a, b):\n    return a - b\n",
                      worker_outcome="success", structured={"tests_pass": True, "status": "SUCCEEDED"})
    assert out.status != "SUCCEEDED"


def test_proof_receipt_records_test_execution_type(migrated):
    aid, out = _drive(migrated, "tx-proof", candidate_src="def add(a, b):\n    return a + b\n")
    with migrated.cursor() as cur:
        cur.execute("SELECT verification_type, result FROM proof_receipt WHERE action_request_id = %s "
                    "ORDER BY created_at DESC LIMIT 1", (aid,))
        vtype, result = cur.fetchone()
    assert vtype == "TEST_EXECUTION" and result == "VERIFIED"


def _evidence_event(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT payload FROM audit_event WHERE action_id = %s "
                    "AND event_type = 'factory.test_execution_evidence' ORDER BY occurred_at DESC LIMIT 1",
                    (aid,))
        row = cur.fetchone()
    return row[0] if row else None


def test_dynamic_execution_evidence_is_durably_auditable(migrated):
    # The transient sandbox fields must be READABLE from durable state after the process
    # exits, WITHOUT re-executing the sandbox.
    aid, out = _drive(migrated, "tx-audit", candidate_src="def add(a, b):\n    return a + b\n")
    ev = _evidence_event(migrated, aid)
    assert ev is not None
    assert ev["verdict"] == "VERIFIED"
    assert ev["terminal_state"] == "COMPLETED" and ev["exit_code"] == 0 and ev["harness_result"] == "PASS"
    assert ev["tests_total"] == 4 and ev["tests_passed"] == 4
    assert ev["runner_kind"] == te.RUNNER_KIND and ev["runner_version"] == te.RUNNER_VERSION
    assert len(ev["candidate_sha256"]) == 64 and len(ev["test_sha256"]) == 64
    assert ev.get("evidence_hash")


def test_rejected_run_also_leaves_durable_evidence(migrated):
    aid, out = _drive(migrated, "tx-audit-rej", candidate_src="def add(a, b):\n    return a * b\n")
    ev = _evidence_event(migrated, aid)
    assert ev is not None and ev["verdict"] == "REJECTED"
    assert ev["terminal_state"] == "COMPLETED" and ev["harness_result"] == "FAIL"


def test_evidence_hash_reconstructs_from_durable_state_without_reexecution(migrated):
    # Rebuild the EXACT evidence-hash preimage from canonical PostgreSQL state alone (no
    # sandbox re-run) and prove it equals BOTH the durable audit-event hash AND the Proof
    # Receipt hash.
    from aidan_core.factory.verifiers import VerificationRequest, _evidence_hash

    aid, out = _drive(migrated, "tx-recon", candidate_src="def add(a, b):\n    return a + b\n")
    with migrated.cursor() as cur:
        cur.execute("SELECT verifier_kind, spec_hash, expected_output_contract FROM execution_spec "
                    "WHERE action_request_id = %s", (aid,))
        verifier_kind, spec_hash, contract = cur.fetchone()
        cur.execute("SELECT execution_attempt_id, evidence_hash FROM proof_receipt "
                    "WHERE action_request_id = %s ORDER BY created_at DESC LIMIT 1", (aid,))
        pr_attempt, pr_hash = cur.fetchone()
    ev = _evidence_event(migrated, aid)
    full = ev["evidence"]                       # full machine-owned evidence, durable
    # Reconstruct the request the verifier used — every field from durable state.
    req = VerificationRequest(
        action_request_id=str(aid), execution_attempt_id=ev["attempt_id"],
        verifier_kind=verifier_kind, expected_output_contract=contract,
        worker_structured_output={}, artifacts=(), spec_hash=spec_hash,
        test_evidence=full, candidate_content_hash=ev["candidate_content_hash"])
    recomputed = _evidence_hash(req, {"evidence": full, "candidate": ev["candidate_content_hash"]})
    assert recomputed == ev["evidence_hash"] == pr_hash
    assert str(pr_attempt) == ev["attempt_id"]


def test_executed_candidate_bytes_are_the_durable_captured_candidate(migrated):
    # Candidate-execution binding: the bytes the sandbox executed are content-addressed to
    # the DURABLE captured candidate (execution_result), not any external/mutated workspace —
    # verify_and_complete rebuilds the ephemeral workspace from canonical state every run.
    from aidan_core.factory.test_execution import canonical_candidate_hash

    aid, out = _drive(migrated, "tx-bind", candidate_src="def add(a, b):\n    return a + b\n")
    with migrated.cursor() as cur:
        cur.execute("SELECT raw_payload FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC LIMIT 1", (aid,))
        raw = cur.fetchone()[0]
    candidate_files = {a["artifact_key"]: a["content"] for a in raw.get("artifacts", [])
                       if isinstance(a.get("content"), str)}
    ev = _evidence_event(migrated, aid)["evidence"]
    assert ev["candidate_sha256"] == canonical_candidate_hash(candidate_files)
