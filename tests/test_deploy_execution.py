"""Gate 6 / Slice 2 — deterministic deployment evidence + VERIFIED Proof Receipt.

A quality-qualified release executes through the existing WorkerAdapter runtime,
materializes into a controlled venture-isolated LOCAL target, and obtains canonical
deployment SUCCESS ONLY from a deterministic verifier that independently inspects the
target and creates the existing proof_receipt. Worker self-report is inert; execution
success ≠ deployment success; no lifecycle transition, no deployment_record, no
external provider/network.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from aidan_core.build import quality as quality_mod
from aidan_core.deploy import checks as checks_mod
from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy.runtime import deploy_target_path
from aidan_core.deploy.verifiers import DeploymentReleaseVerifier
from aidan_core.errors import NotFoundError
from aidan_core.factory.verifiers import VerificationRequest
from factory_fakes import ErrorWorker, registry_with

from deploy_fakes import DeployBundleWorker, DeployBundleWorkerB, setup_deploy


def _release(conn, s, **kw):
    return release_mod.create_release_candidate(
        conn, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
        deployment_target_id=s.target_id, **kw)


def _deploy(conn, s, *, worker, max_attempts=1):
    return deploy_runtime.execute_deploy(
        conn, s.deploy_action_id, registry=registry_with(worker), worker_kind=worker.kind,
        max_attempts=max_attempts)


def _verify(conn, s):
    return deploy_runtime.verify_deploy(conn, s.deploy_action_id, actual_cost=0)


def _proofs(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT result, verification_type, execution_attempt_id FROM proof_receipt "
                    "WHERE action_request_id = %s", (aid,))
        return cur.fetchall()


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def _run(conn, slug, *, mode="compliant", release_contract=None, provider="fake-a", key=None):
    s = setup_deploy(conn, slug, key=key or slug, provider_kind=provider)
    _release(conn, s, release_contract=release_contract)
    worker = DeployBundleWorker(mode=mode)
    _deploy(conn, s, worker=worker)
    return s, worker


# ==========================================================================
# Happy path: exact release verified -> proof (matrix 1-8)
# ==========================================================================
def test_1_8_verified_deployment_creates_proof(migrated):
    s, _ = _run(migrated, "x1")
    out = _verify(migrated, s)
    assert out.status == "SUCCEEDED" and out.verified is True
    proofs = _proofs(migrated, s.deploy_action_id)
    assert len(proofs) == 1 and proofs[0][0] == "VERIFIED" and proofs[0][1] == "DEPLOYMENT_RELEASE"


# ==========================================================================
# Worker self-report cannot manufacture success (matrix 9-11, 18-21)
# ==========================================================================
def test_9_worker_deployed_but_nothing_written_fails(migrated):
    s, _ = _run(migrated, "x9", mode="nothing")
    out = _verify(migrated, s)
    assert out.verified is False and out.status != "SUCCEEDED"
    assert all(r[0] != "VERIFIED" for r in _proofs(migrated, s.deploy_action_id))


def test_10_worker_wrong_bytes_release_identity_fail(migrated):
    s, _ = _run(migrated, "x10", mode="wrong_bytes")
    out = _verify(migrated, s)
    assert out.verified is False
    assert not any(r[0] == "VERIFIED" for r in _proofs(migrated, s.deploy_action_id))


def test_11_extra_file_changes_identity_fail(migrated):
    s = setup_deploy(migrated, "x11")
    _release(migrated, s)
    worker = DeployBundleWorker(mode="compliant")
    _deploy(migrated, s, worker=worker)
    # deploy an EXTRA file into the target after the compliant write -> tree hash changes
    extra = os.path.join(deploy_target_path(s.venture_id, s.target_id), "release", "app", "rogue.py")
    with open(extra, "wb") as f:
        f.write(b"print('rogue')")
    out = _verify(migrated, s)
    assert out.verified is False


def test_12_target_tamper_after_deploy_fails(migrated):
    s, _ = _run(migrated, "x12", mode="compliant")
    main = os.path.join(deploy_target_path(s.venture_id, s.target_id), "release", "app", "main.py")
    with open(main, "wb") as f:
        f.write(b"TAMPERED-ON-TARGET")
    out = _verify(migrated, s)
    assert out.verified is False


def test_18_execution_succeeded_but_verification_fail_no_proof(migrated):
    # worker execution succeeds (result captured), but deployed bytes are wrong
    s, worker = _run(migrated, "x18", mode="wrong_bytes")
    assert worker.calls == 1  # worker executed successfully
    out = _verify(migrated, s)
    assert out.verified is False and out.status != "SUCCEEDED"


def test_19_execution_failed_no_deployment_proof(migrated):
    s = setup_deploy(migrated, "x19")
    _release(migrated, s)
    _deploy(migrated, s, worker=ErrorWorker())  # worker raises -> WORKER_ERROR, no result
    with pytest.raises(NotFoundError):
        _verify(migrated, s)
    assert _proofs(migrated, s.deploy_action_id) == []


def test_20_21_worker_self_report_and_lifecycle_claim_inert(migrated):
    s = setup_deploy(migrated, "x20")
    _release(migrated, s)
    worker = DeployBundleWorker(mode="nothing", structured_output={
        "deployed": True, "release_verified": True, "health": True,
        "overall_success": True, "lifecycle": "OPERATING"})
    _deploy(migrated, s, worker=worker)
    out = _verify(migrated, s)
    assert out.verified is False
    assert _lifecycle(migrated, s.venture_id) == "DISCOVERED"


# ==========================================================================
# Health / runtime false positives (matrix 13-15)
# ==========================================================================
def test_13_healthy_wrong_release_fails(migrated):
    s, _ = _run(migrated, "x13", mode="wrong_bytes")  # writes health marker + wrong bytes
    tp = deploy_target_path(s.venture_id, s.target_id)
    results = {c.name: c.result for c in checks_mod.evaluate(
        tp, venture_id=s.venture_id, deployment_target_id=s.target_id,
        expected_tree_hash=_expected_hash(migrated, s), release_contract={})}
    assert results["HEALTH"] == "PASS" and results["RELEASE_IDENTITY"] == "FAIL"
    assert _verify(migrated, s).verified is False


def test_14_exact_release_unhealthy_fails(migrated):
    s, _ = _run(migrated, "x14", mode="no_health")  # correct bytes, no health marker
    tp = deploy_target_path(s.venture_id, s.target_id)
    results = {c.name: c.result for c in checks_mod.evaluate(
        tp, venture_id=s.venture_id, deployment_target_id=s.target_id,
        expected_tree_hash=_expected_hash(migrated, s), release_contract={})}
    assert results["RELEASE_IDENTITY"] == "PASS" and results["HEALTH"] == "FAIL"
    assert _verify(migrated, s).verified is False


def test_15_runtime_contract_missing_fails(migrated):
    # exact bytes + health, but the required entry artifact is not present -> FAIL
    s, _ = _run(migrated, "x15", mode="compliant", release_contract={"entry_artifact": "app/server.py"})
    out = _verify(migrated, s)
    assert out.verified is False


def _expected_hash(conn, s):
    with conn.cursor() as cur:
        cur.execute("SELECT candidate_tree_hash FROM build_manifest WHERE id = %s", (s.build_manifest_id,))
        return cur.fetchone()[0]


# ==========================================================================
# Venture isolation (matrix 16-17) — verifier unit checks
# ==========================================================================
def test_16_17_venture_target_isolation(tmp_path):
    good = str(tmp_path / "venture-A" / "target-1")
    r = checks_mod.check_venture_isolation(good, "venture-A", "target-1")
    assert r.result == "PASS"
    wrong = str(tmp_path / "venture-B" / "target-1")
    r2 = checks_mod.check_venture_isolation(wrong, "venture-A", "target-1")
    assert r2.result == "FAIL"  # identical bytes on a wrong-venture path do not verify


def test_isolation_blocks_verification_even_with_identity(tmp_path):
    # craft a verification request whose target_path venture != contract venture
    v = DeploymentReleaseVerifier()
    req = VerificationRequest(
        action_request_id="a", execution_attempt_id="e", verifier_kind="deployment-release",
        expected_output_contract={"deployment": {
            "target_path": str(tmp_path / "venture-B" / "t"), "venture_id": "venture-A",
            "deployment_target_id": "t", "candidate_tree_hash": "h", "release_contract": {}}},
        worker_structured_output={}, artifacts=(), spec_hash="s")
    assert v.verify(req).verdict == "REJECTED"


# ==========================================================================
# Failure semantics + proof provenance (matrix 22-27)
# ==========================================================================
def test_22_23_failure_leaves_quality_and_lifecycle(migrated):
    s, _ = _run(migrated, "x22", mode="wrong_bytes")
    _verify(migrated, s)
    assert quality_mod.overall_verdict(migrated, s.build_manifest_id) == "PASS"  # Gate-5 intact
    assert _lifecycle(migrated, s.venture_id) == "DISCOVERED"


def test_24_25_proof_bound_to_release_and_attempt(migrated):
    s, _ = _run(migrated, "x24")
    out = _verify(migrated, s)
    assert out.verified is True
    with migrated.cursor() as cur:
        cur.execute("SELECT execution_attempt_id FROM proof_receipt WHERE action_request_id = %s "
                    "AND result = 'VERIFIED'", (s.deploy_action_id,))
        proof_attempt = cur.fetchone()[0]
        cur.execute("SELECT id FROM execution_attempt WHERE action_request_id = %s", (s.deploy_action_id,))
        attempt_id = cur.fetchone()[0]
    assert str(proof_attempt) == str(attempt_id)


def test_26_verified_deployment_is_exactly_once(migrated):
    s, _ = _run(migrated, "x26")
    _verify(migrated, s)
    _verify(migrated, s)  # converges (idempotent completion)
    verified = [r for r in _proofs(migrated, s.deploy_action_id) if r[0] == "VERIFIED"]
    assert len(verified) == 1


# ==========================================================================
# Retry + lifecycle + neutrality + scope (matrix 28-32)
# ==========================================================================
def test_28_retry_same_release_new_attempt(migrated):
    s = setup_deploy(migrated, "x28")
    rc = _release(migrated, s)
    _deploy(migrated, s, worker=DeployBundleWorker(mode="wrong_bytes"), max_attempts=2)
    a1, e1 = _latest_attempt_and_result(migrated, s.deploy_action_id)
    assert _verify(migrated, s).verified is False           # attempt 1 rejected, retryable
    _deploy(migrated, s, worker=DeployBundleWorker(mode="compliant"), max_attempts=2)
    a2, e2 = _latest_attempt_and_result(migrated, s.deploy_action_id)
    out = _verify(migrated, s)
    assert out.status == "SUCCEEDED" and out.verified is True
    # same immutable release; new distinct attempt; distinct external result identity
    assert release_mod.get_release_candidate(migrated, s.deploy_action_id)[8] == rc.release_hash
    assert a1 != a2 and e1 != e2
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (s.deploy_action_id,))
        assert cur.fetchone()[0] == 2                       # attempt-1 history retained
        # the VERIFIED proof is bound to the successful (second) attempt
        cur.execute("SELECT execution_attempt_id FROM proof_receipt WHERE action_request_id = %s "
                    "AND result = 'VERIFIED'", (s.deploy_action_id,))
        assert str(cur.fetchone()[0]) == str(a2)


def _latest_attempt_and_result(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT execution_attempt_id, external_result_id FROM execution_result "
                    "WHERE action_request_id = %s ORDER BY received_at DESC, id DESC LIMIT 1", (aid,))
        row = cur.fetchone()
        return (str(row[0]), row[1]) if row else (None, None)


def test_30_lifecycle_unchanged_after_verified_deploy(migrated):
    s, _ = _run(migrated, "x30")
    assert _verify(migrated, s).verified is True
    assert _lifecycle(migrated, s.venture_id) == "DISCOVERED"  # no BUILDING->OPERATING in Slice 2


def test_29_no_deployment_record_table(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.deployment_record')")
        assert cur.fetchone()[0] is None


def test_32_provider_neutral_same_verifier(migrated):
    a, _ = _run(migrated, "x32a", provider="fake-a", key="x32a")
    assert _verify(migrated, a).verified is True
    b = setup_deploy(migrated, "x32b", key="x32b", provider_kind="fake-b")
    _release(migrated, b)
    _deploy(migrated, b, worker=DeployBundleWorkerB(mode="compliant"))
    assert _verify(migrated, b).verified is True
