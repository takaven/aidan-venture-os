"""Gate 6 / Slice 1 — governed release authority + canonical deployment binding.

Proves: a Gate-5 quality PASS is not deployment authority; an immutable,
quality-qualified, venture-bound release_candidate + deployment_target must be frozen
and the generic Gate-4 execution_spec bound to them — enforced at
factory.spec.create_execution_spec for ANY caller — before a deploy worker (an ordinary
Gate-4 WorkerAdapter) may be dispatched. No external deploy, verification, proof,
deployment_record, or lifecycle transition.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import approvals, execution
from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import target as target_mod
from aidan_core.deploy.runtime import DeployInput
from aidan_core.errors import (
    ApprovalRequiredError,
    DeployAuthorityError,
    IdempotencyConflictError,
)
from aidan_core.factory import runtime as factory_runtime
from aidan_core.factory import spec as spec_mod

from build_fakes import GOOD_PRODUCT_MANIFEST, full_eval
from deploy_fakes import DeployWorker, DeployWorkerB, deploy_action, setup_deploy
from factory_fakes import FakeWorkerA, registry_with, spec_action

_MF = GOOD_PRODUCT_MANIFEST


def _mf(**over):
    d = dict(GOOD_PRODUCT_MANIFEST)
    d.update(over)
    return d


def _release(conn, s, **kw):
    return release_mod.create_release_candidate(
        conn, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
        deployment_target_id=s.target_id, **kw)


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def _blocked_release(conn, slug, **eval_kwargs):
    r = full_eval(conn, slug, key=slug, **eval_kwargs)
    t = target_mod.register_deployment_target(conn, r.auth.venture_id, environment="staging",
                                              provider_kind="fake-a", target_ref=f"deploy://{slug}/s")
    aid = deploy_action(conn, r.auth.venture_id, key=slug)
    return r, t, aid


# ==========================================================================
# Deployment target (matrix 1-6)
# ==========================================================================
def test_1_target_registered(migrated):
    s = setup_deploy(migrated, "d1")
    row = target_mod.get_deployment_target(migrated, s.target_id)
    assert row is not None and row[2] == "staging" and row[3] == "fake-a"


def test_2_target_exact_replay_converges(migrated):
    s = setup_deploy(migrated, "d2")
    again = target_mod.register_deployment_target(
        migrated, s.venture_id, environment="staging", provider_kind="fake-a", target_ref="deploy://d2/staging")
    assert again.created is False and again.deployment_target_id == s.target_id


def test_3_target_changed_ref_conflicts(migrated):
    s = setup_deploy(migrated, "d3")
    with pytest.raises(IdempotencyConflictError):
        target_mod.register_deployment_target(
            migrated, s.venture_id, environment="staging", provider_kind="fake-a", target_ref="deploy://d3/OTHER")


def test_4_different_environment_is_distinct_target(migrated):
    s = setup_deploy(migrated, "d4")
    prod = target_mod.register_deployment_target(
        migrated, s.venture_id, environment="prod", provider_kind="fake-a", target_ref="deploy://d4/prod")
    assert prod.created is True and prod.deployment_target_id != s.target_id


def test_5_target_changed_provider_conflicts(migrated):
    s = setup_deploy(migrated, "d5")
    with pytest.raises(IdempotencyConflictError):
        target_mod.register_deployment_target(
            migrated, s.venture_id, environment="staging", provider_kind="OTHER", target_ref="deploy://d5/staging")


def test_6_target_rejects_secret_body(migrated):
    s = setup_deploy(migrated, "d6")
    with pytest.raises(ValueError):
        target_mod.register_deployment_target(
            migrated, s.venture_id, environment="prod", provider_kind="fake-a", target_ref="deploy://d6/prod",
            provenance={"api_key": "SECRET"})


# ==========================================================================
# Release candidate + quality prerequisite (matrix 7-20)
# ==========================================================================
def test_7_qualified_manifest_creates_release(migrated):
    s = setup_deploy(migrated, "d7")
    res = _release(migrated, s)
    assert res.created is True and res.release_hash


@pytest.mark.parametrize("slug,kw", [
    ("d8", dict(candidate_files=[{"path": "readme.txt", "content": "x"}])),        # Technical FAIL
    ("d9", dict(product_manifest=_mf(features=["approval_tracking"]))),            # Product FAIL
    ("d10", dict(product_manifest=_mf(dead_ends=["x"]))),                          # Experience FAIL
    ("d11", dict(product_manifest=_mf(buyer="someone else"))),                     # Commercial FAIL
    ("d12", dict(product_manifest=_mf(features=["dashboard"], differentiators_implemented=[]))),  # AntiGeneric FAIL
])
def test_8_12_dimension_fail_blocks_release(migrated, slug, kw):
    r, t, aid = _blocked_release(migrated, slug, **kw)
    with pytest.raises(DeployAuthorityError):
        release_mod.create_release_candidate(
            migrated, aid, build_manifest_id=r.manifest_id, deployment_target_id=t.deployment_target_id)
    assert release_mod.get_release_candidate(migrated, aid) is None


def test_13_release_exact_replay_converges(migrated):
    s = setup_deploy(migrated, "d13")
    first = _release(migrated, s)
    again = _release(migrated, s)
    assert again.created is False and again.release_candidate_id == first.release_candidate_id


def test_14_changed_candidate_conflicts(migrated):
    s = setup_deploy(migrated, "d14")
    _release(migrated, s)
    other = full_eval(migrated, "d14b", key="d14b", product_manifest=GOOD_PRODUCT_MANIFEST)
    with pytest.raises(IdempotencyConflictError):
        release_mod.create_release_candidate(
            migrated, s.deploy_action_id, build_manifest_id=other.manifest_id, deployment_target_id=s.target_id)


def test_15_changed_target_conflicts(migrated):
    s = setup_deploy(migrated, "d15")
    _release(migrated, s)
    prod = target_mod.register_deployment_target(
        migrated, s.venture_id, environment="prod", provider_kind="fake-a", target_ref="deploy://d15/prod")
    with pytest.raises(IdempotencyConflictError):
        release_mod.create_release_candidate(
            migrated, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
            deployment_target_id=prod.deployment_target_id)


def test_16_changed_release_contract_conflicts(migrated):
    s = setup_deploy(migrated, "d16")
    _release(migrated, s, release_contract={"runtime_kind": "node"})
    with pytest.raises(IdempotencyConflictError):
        _release(migrated, s, release_contract={"runtime_kind": "python"})


def test_17_release_hash_binds_full_intent():
    base = dict(venture_id="v", build_manifest_id="m", build_spec_id="b", candidate_tree_hash="t",
                deployment_target_id="tg", environment="staging", provider_kind="fake-a",
                target_ref="deploy://x", release_contract={})
    h = release_mod.compute_release_hash(**base)
    assert release_mod.compute_release_hash(**dict(base, environment="prod")) != h        # target env matters
    assert release_mod.compute_release_hash(**dict(base, target_ref="deploy://y")) != h    # target ref matters
    assert release_mod.compute_release_hash(**dict(base, release_contract={"runtime_kind": "node"})) != h
    assert release_mod.compute_release_hash(**dict(base, candidate_tree_hash="t2")) != h    # candidate matters


def test_18_cross_venture_manifest_rejected(migrated):
    s = setup_deploy(migrated, "d18a", key="d18a")
    other = full_eval(migrated, "d18b", key="d18b", product_manifest=GOOD_PRODUCT_MANIFEST)
    with pytest.raises(DeployAuthorityError):  # other venture's manifest
        release_mod.create_release_candidate(
            migrated, s.deploy_action_id, build_manifest_id=other.manifest_id, deployment_target_id=s.target_id)


def test_19_cross_venture_target_rejected(migrated):
    s = setup_deploy(migrated, "d19a", key="d19a")
    other = setup_deploy(migrated, "d19b", key="d19b")
    with pytest.raises(DeployAuthorityError):  # other venture's target
        release_mod.create_release_candidate(
            migrated, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
            deployment_target_id=other.target_id)


def test_20_non_deploy_action_cannot_own_release(migrated):
    s = setup_deploy(migrated, "d20")
    vid, spend_aid = None, None
    with migrated.cursor() as cur:
        cur.execute("INSERT INTO action_request (venture_id, action_type, actor, payload, payload_hash, "
                    "idempotency_key, required_autonomy, requested_amount, requested_currency) "
                    "VALUES (%s, 'spend', 'a', '{}'::jsonb, 'h', 'spend:d20', 0, 0, 'USD') RETURNING id",
                    (s.venture_id,))
        spend_aid = cur.fetchone()[0]
    with pytest.raises(DeployAuthorityError):
        release_mod.create_release_candidate(
            migrated, spend_aid, build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)


# ==========================================================================
# Canonical generic-Factory deploy guard (matrix 21-28)
# ==========================================================================
def _spec(conn, aid, task_payload):
    return spec_mod.create_execution_spec(
        conn, aid, worker_kind="deploy-a", verifier_kind="structured-contract",
        timeout_seconds=60, max_attempts=1, capability_scope=["DEPLOY_CANDIDATE"], task_payload=task_payload)


def test_21_A1_no_release_rejected(migrated):
    s = setup_deploy(migrated, "d21")  # deploy action exists, NO release_candidate
    with pytest.raises(DeployAuthorityError):
        _spec(migrated, s.deploy_action_id, {"instruction": "deploy whatever is in the workspace"})
    assert spec_mod.get_execution_spec(migrated, s.deploy_action_id) is None


def test_22_A2_fabricated_binding_rejected(migrated):
    s = setup_deploy(migrated, "d22")
    with pytest.raises(DeployAuthorityError):
        _spec(migrated, s.deploy_action_id, {"deploy": {
            "release_candidate_id": "00000000-0000-0000-0000-000000000000",
            "release_hash": "deadbeef", "deployment_target_id": "00000000-0000-0000-0000-000000000000"}})


def test_23_A3_wrong_hash_rejected(migrated):
    s = setup_deploy(migrated, "d23")
    rc = _release(migrated, s)
    with pytest.raises(DeployAuthorityError):
        _spec(migrated, s.deploy_action_id, {"deploy": {
            "release_candidate_id": str(rc.release_candidate_id), "release_hash": "wrong",
            "deployment_target_id": str(s.target_id)}})


def test_24_A4_wrong_target_rejected(migrated):
    s = setup_deploy(migrated, "d24")
    rc = _release(migrated, s)
    prod = target_mod.register_deployment_target(
        migrated, s.venture_id, environment="prod", provider_kind="fake-a", target_ref="deploy://d24/prod")
    with pytest.raises(DeployAuthorityError):
        _spec(migrated, s.deploy_action_id, {"deploy": {
            "release_candidate_id": str(rc.release_candidate_id), "release_hash": rc.release_hash,
            "deployment_target_id": str(prod.deployment_target_id)}})


def test_25_A5_cross_venture_release_binding_rejected(migrated):
    a = setup_deploy(migrated, "d25a", key="d25a")
    b = setup_deploy(migrated, "d25b", key="d25b")
    rc_b = _release(migrated, b)
    with pytest.raises(DeployAuthorityError):  # A's action bound to B's release
        _spec(migrated, a.deploy_action_id, {"deploy": {
            "release_candidate_id": str(rc_b.release_candidate_id), "release_hash": rc_b.release_hash,
            "deployment_target_id": str(b.target_id)}})


def test_26_A6_valid_binding_creates_spec(migrated):
    s = setup_deploy(migrated, "d26")
    _release(migrated, s)
    dispatch = deploy_runtime.prepare_deploy_execution(
        migrated, s.deploy_action_id, worker_kind="deploy-a", verifier_kind="structured-contract",
        timeout_seconds=60, max_attempts=1)
    row = spec_mod.get_execution_spec(migrated, s.deploy_action_id)
    block = row[4]["deploy"]
    assert block["release_hash"] == dispatch.release_hash
    assert block["deployment_target_id"] == str(s.target_id)


def test_27_A7_non_deploy_action_unaffected(migrated):
    vid, aid, sp = spec_action(migrated, "d27", verifier_kind="structured-contract",
                               expected_output_contract={"require": {"status": "done"}})
    worker = FakeWorkerA(structured_output={"status": "done"})
    res = factory_runtime.execute_action(migrated, aid, registry=registry_with(worker))
    assert res.dispatched is True and worker.calls == 1


# ==========================================================================
# Authorization chronology (matrix 29-30)
# ==========================================================================
def test_29_30_pre_release_authorization_insufficient(migrated):
    r = full_eval(migrated, "d29", key="d29", product_manifest=GOOD_PRODUCT_MANIFEST)
    t = target_mod.register_deployment_target(migrated, r.auth.venture_id, environment="staging",
                                              provider_kind="fake-a", target_ref="deploy://d29/s")
    # deploy action requiring approval (autonomy 2 > venture autonomy 1)
    aid = deploy_action(migrated, r.auth.venture_id, key="d29", required_autonomy=2)
    release_mod.create_release_candidate(migrated, aid, build_manifest_id=r.manifest_id,
                                         deployment_target_id=t.deployment_target_id)
    pre = execution.request_execution(migrated, aid)          # pre-spec approval
    approvals.approve(migrated, pre.approval_id, decided_by="board")
    worker = DeployWorker()
    with pytest.raises(ApprovalRequiredError):
        deploy_runtime.execute_deploy(migrated, aid, registry=registry_with(worker),
                                      worker_kind="deploy-a", verifier_kind="structured-contract")
    assert worker.calls == 0
    fresh = factory_runtime.request_dispatch_authorization(migrated, aid)  # post-spec
    approvals.approve(migrated, fresh.approval_id, decided_by="board")
    _, res = deploy_runtime.execute_deploy(migrated, aid, registry=registry_with(worker),
                                           worker_kind="deploy-a", verifier_kind="structured-contract")
    assert res.dispatched is True and worker.calls == 1


# ==========================================================================
# Worker architecture + authority + no side effects (matrix 31-38)
# ==========================================================================
def _dispatch(conn, s, worker):
    _release(conn, s)
    return deploy_runtime.execute_deploy(conn, s.deploy_action_id, registry=registry_with(worker),
                                         worker_kind=worker.kind, verifier_kind="structured-contract")


def test_31_deploy_worker_uses_workeradapter(migrated):
    s = setup_deploy(migrated, "d31")
    worker = DeployWorker()
    _dispatch(migrated, s, worker)
    assert worker.calls == 1
    req = worker.last_request
    assert not hasattr(req, "conn") and not hasattr(req, "connection")
    di = DeployInput.from_worker_request(req)
    assert di.deployment_target_id == str(s.target_id)


def test_32_37_worker_self_report_inert(migrated):
    s = setup_deploy(migrated, "d32")
    rc_before = release_mod.get_release_candidate(migrated, s.deploy_action_id)  # None yet
    worker = DeployWorker(structured_output={
        "deployed": True, "release_verified": True, "lifecycle": "OPERATING", "overall_success": True})
    _dispatch(migrated, s, worker)
    rc = release_mod.get_release_candidate(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "DISCOVERED"               # [33] lifecycle unchanged
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s", (s.deploy_action_id,))
        assert cur.fetchone()[0] == 0                                       # [36] no deployment proof
        cur.execute("SELECT to_regclass('public.deployment_record')")
        assert cur.fetchone()[0] is None                                    # [35] no deployment_record
    # [34] release_candidate unchanged; [37] build quality unchanged
    from aidan_core.build import quality as quality_mod
    assert quality_mod.overall_verdict(migrated, s.build_manifest_id) == "PASS"


def test_38_31_provider_neutral_same_path(migrated):
    a = setup_deploy(migrated, "d38a", key="d38a", provider_kind="fake-a")
    b = setup_deploy(migrated, "d38b", key="d38b", provider_kind="fake-b")
    wa, wb = DeployWorker(), DeployWorkerB()
    _, ra = _dispatch(migrated, a, wa)
    _, rb = _dispatch(migrated, b, wb)
    assert ra.dispatched and rb.dispatched and wa.calls == 1 and wb.calls == 1
    assert ra.worker_kind == "deploy-a" and rb.worker_kind == "deploy-b"


def test_capability_unknown_rejected(migrated):
    s = setup_deploy(migrated, "dcap")
    _release(migrated, s)
    with pytest.raises(ValueError):  # unknown capability rejected by kernel vocabulary
        spec_mod.create_execution_spec(
            migrated, s.deploy_action_id, worker_kind="deploy-a", verifier_kind="structured-contract",
            timeout_seconds=60, max_attempts=1, capability_scope=["DEPLOY_TO_PROD"], task_payload={})
