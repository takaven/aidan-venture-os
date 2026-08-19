"""Gate 6 / Slice 4 — development deployment/recovery evals.

Each eval drives the FULL production chain (Gate-5-qualified build -> deployment_target
-> release_candidate -> execution_spec -> WorkerAdapter -> execution -> real local target
-> deterministic verifier -> proof_receipt -> proof-gated lifecycle), asserting canonical
state (proof + lifecycle) rather than worker claims. Worker fakes choose only observable
target behaviors; expected outcomes live in assertions.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from aidan_core import killswitch
from aidan_core.build import quality as quality_mod
from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import state as deploy_state
from aidan_core.deploy import target as target_mod
from aidan_core.deploy.runtime import deploy_target_path
from aidan_core.errors import DeployAuthorityError, ExecutionBlockedError, IdempotencyConflictError
from aidan_core.factory import runtime as factory_runtime
from aidan_core.factory import spec as spec_mod

from build_fakes import GOOD_PRODUCT_MANIFEST, full_eval
from deploy_fakes import (
    DeployBundleWorker,
    DeployBundleWorkerB,
    deploy_action,
    run_deploy,
    second_qualified_manifest,
    setup_deploy,
    to_building,
)
from factory_fakes import FakeWorkerA, registry_with, spec_action


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def _verified_proofs(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT execution_attempt_id FROM proof_receipt WHERE action_request_id = %s "
                    "AND result = 'VERIFIED' AND verification_type = 'DEPLOYMENT_RELEASE'", (aid,))
        return [str(r[0]) for r in cur.fetchall()]


def _promote(conn, s):
    return deploy_state.promote_verified_deployment(conn, s.deploy_action_id)


def _mf(**o):
    d = dict(GOOD_PRODUCT_MANIFEST)
    d.update(o)
    return d


# ---- A: exact success -> OPERATING ---------------------------------------------------
def test_A_exact_deployment_promotes(migrated):
    r = run_deploy(migrated, "eA")
    assert r.verify.verified is True
    assert _promote(migrated, r.s)["state"] == "OPERATING"
    assert _lifecycle(migrated, r.s.venture_id) == "OPERATING"


# ---- B-H: verification failure modes (no proof, stays BUILDING) ----------------------
@pytest.mark.parametrize("slug,mode,contract", [
    ("eB", "nothing", None),          # worker claims deployed, writes nothing
    ("eC", "wrong_bytes", None),      # wrong bytes
    ("eF", "wrong_bytes", None),      # healthy but wrong release (health written + wrong bytes)
    ("eG", "no_health", None),        # exact bytes, unhealthy
    ("eH", "compliant", {"entry_artifact": "app/server.py"}),  # runtime contract missing
])
def test_B_H_verification_failures_stay_building(migrated, slug, mode, contract):
    r = run_deploy(migrated, slug, mode=mode, release_contract=contract)
    assert r.verify.verified is False
    assert _verified_proofs(migrated, r.s.deploy_action_id) == []
    with pytest.raises(DeployAuthorityError):
        _promote(migrated, r.s)
    assert _lifecycle(migrated, r.s.venture_id) == "BUILDING"


def test_D_same_venture_wrong_release_identity_fail(migrated):
    # worker deploys, then the target holds a DIFFERENT same-venture candidate's bytes
    # BEFORE verification -> identity (release_candidate) controls truth, not venture.
    s = setup_deploy(migrated, "eD")
    release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                         build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)
    to_building(migrated, s.venture_id)
    w = DeployBundleWorker(mode="compliant")
    deploy_runtime.execute_deploy(migrated, s.deploy_action_id, registry=registry_with(w), worker_kind=w.kind)
    main = os.path.join(deploy_target_path(s.venture_id, s.target_id), "release", "app", "main.py")
    with open(main, "wb") as f:
        f.write(b"def run():\n    return 'A-DIFFERENT-CANDIDATE'\n")
    assert deploy_runtime.verify_deploy(migrated, s.deploy_action_id, actual_cost=0).verified is False


def test_E_target_tamper_after_completion_fails(migrated):
    s = setup_deploy(migrated, "eE")
    release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                         build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)
    to_building(migrated, s.venture_id)
    w = DeployBundleWorker(mode="compliant")
    deploy_runtime.execute_deploy(migrated, s.deploy_action_id, registry=registry_with(w), worker_kind=w.kind)
    main = os.path.join(deploy_target_path(s.venture_id, s.target_id), "release", "app", "main.py")
    with open(main, "wb") as f:
        f.write(b"TAMPERED")
    assert deploy_runtime.verify_deploy(migrated, s.deploy_action_id, actual_cost=0).verified is False


# ---- I-J: venture isolation ----------------------------------------------------------
def test_I_cross_venture_target_rejected_at_release(migrated):
    a = setup_deploy(migrated, "eIa", key="eIa")
    b = setup_deploy(migrated, "eIb", key="eIb")
    with pytest.raises(DeployAuthorityError):  # A's action bound to B's target
        release_mod.create_release_candidate(migrated, a.deploy_action_id,
                                             build_manifest_id=a.build_manifest_id, deployment_target_id=b.target_id)


def test_J_identical_bytes_wrong_venture_rejected(migrated):
    from aidan_core.deploy import checks as checks_mod
    # even with matching identity, a wrong-venture target path fails isolation
    r = checks_mod.check_venture_isolation("/tmp/aidan-deploy/venture-B/t", "venture-A", "t")
    assert r.result == "FAIL"


# ---- K-N, O-P: proof/lifecycle authority --------------------------------------------
def test_K_generic_execution_proof_insufficient(migrated):
    r = full_eval(migrated, "eK", product_manifest=GOOD_PRODUCT_MANIFEST)
    factory_runtime.verify_and_complete(migrated, r.auth.action_id, actual_cost=0)  # STRUCTURED_CONTRACT proof
    to_building(migrated, r.auth.venture_id)
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, r.auth.action_id)
    assert _lifecycle(migrated, r.auth.venture_id) == "BUILDING"


def test_O_P_worker_lifecycle_and_verification_claims_inert(migrated):
    r = run_deploy(migrated, "eOP", mode="nothing", structured_output={
        "deployed": True, "verified": True, "health": True, "overall_success": True, "lifecycle": "OPERATING"})
    assert r.verify.verified is False
    with pytest.raises(DeployAuthorityError):
        _promote(migrated, r.s)
    assert _lifecycle(migrated, r.s.venture_id) == "BUILDING"


# ---- Q-S: execution vs deployment; failure semantics --------------------------------
def test_Q_execution_succeeded_deployment_fail(migrated):
    r = run_deploy(migrated, "eQ", mode="wrong_bytes")
    assert r.worker.calls == 1                 # worker execution succeeded
    assert r.verify.verified is False and _lifecycle(migrated, r.s.venture_id) == "BUILDING"


def test_R_S_quality_preserved_and_no_autokill(migrated):
    r = run_deploy(migrated, "eR", mode="wrong_bytes")
    assert quality_mod.overall_verdict(migrated, r.s.build_manifest_id) == "PASS"
    for d in ("PRODUCT", "EXPERIENCE", "COMMERCIAL", "ANTIGENERIC"):
        assert quality_mod.dimension_verdict(migrated, r.s.build_manifest_id, d) == "PASS"
    assert killswitch.is_killed(migrated, r.s.venture_id) is False


# ---- T-U: retry ----------------------------------------------------------------------
def test_T_U_retry_same_release_then_promote(migrated):
    s = setup_deploy(migrated, "eT")
    rc = release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                              build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)
    to_building(migrated, s.venture_id)
    deploy_runtime.execute_deploy(migrated, s.deploy_action_id, registry=registry_with(DeployBundleWorker(mode="wrong_bytes")),
                                  worker_kind="deploy-a", max_attempts=2)
    assert deploy_runtime.verify_deploy(migrated, s.deploy_action_id, actual_cost=0).verified is False
    deploy_runtime.execute_deploy(migrated, s.deploy_action_id, registry=registry_with(DeployBundleWorker(mode="compliant")),
                                  worker_kind="deploy-a", max_attempts=2)
    out = deploy_runtime.verify_deploy(migrated, s.deploy_action_id, actual_cost=0)
    assert out.verified is True
    assert deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)["state"] == "OPERATING"
    assert release_mod.get_release_candidate(migrated, s.deploy_action_id)[8] == rc.release_hash
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (s.deploy_action_id,))
        assert cur.fetchone()[0] == 2


# ---- V-X: changed intent is not a retry ---------------------------------------------
def test_V_changed_candidate_not_retry(migrated):
    s = setup_deploy(migrated, "eV")
    release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                         build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)
    m2 = second_qualified_manifest(migrated, s.venture_id, key="eVtwo",
                                   candidate_files=[{"path": "app/main.py", "content": "v2\n"}])
    with pytest.raises(IdempotencyConflictError):
        release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                             build_manifest_id=m2, deployment_target_id=s.target_id)


def test_W_changed_target_not_retry(migrated):
    s = setup_deploy(migrated, "eW")
    release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                         build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)
    t2 = target_mod.register_deployment_target(migrated, s.venture_id, environment="prod",
                                               provider_kind="fake-a", target_ref="deploy://eW/prod")
    with pytest.raises(IdempotencyConflictError):
        release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                             build_manifest_id=s.build_manifest_id,
                                             deployment_target_id=t2.deployment_target_id)


def test_X_changed_contract_not_retry(migrated):
    s = setup_deploy(migrated, "eX")
    release_mod.create_release_candidate(migrated, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
                                         deployment_target_id=s.target_id, release_contract={"runtime_kind": "node"})
    with pytest.raises(IdempotencyConflictError):
        release_mod.create_release_candidate(migrated, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
                                             deployment_target_id=s.target_id, release_contract={"runtime_kind": "python"})


# ---- Y-Z: replay + kill --------------------------------------------------------------
def test_Y_replay_projection_idempotent(migrated):
    r = run_deploy(migrated, "eY")
    _promote(migrated, r.s)
    assert _promote(migrated, r.s)["outcome"] == "already_operating"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_event WHERE venture_id = %s "
                    "AND event_type = 'venture.lifecycle_transition' AND payload->>'to' = 'OPERATING'",
                    (r.s.venture_id,))
        assert cur.fetchone()[0] == 1


def test_Z_kill_after_proof_blocks_promotion(migrated):
    r = run_deploy(migrated, "eZ")
    assert r.verify.verified is True
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        _promote(migrated, r.s)
    assert _lifecycle(migrated, r.s.venture_id) == "BUILDING"


# ---- AA-AB: history + latest derivation ---------------------------------------------
def test_AA_prior_verified_survives_later_failed_release(migrated):
    r = run_deploy(migrated, "eAA")
    _promote(migrated, r.s)  # OPERATING with verified deployment
    # a later, separate deploy action on the same venture/target that FAILS verification
    aid2 = deploy_action(migrated, r.s.venture_id, key="eAA2")
    release_mod.create_release_candidate(migrated, aid2, build_manifest_id=r.s.build_manifest_id,
                                         deployment_target_id=r.s.target_id)
    w = DeployBundleWorker(mode="wrong_bytes")
    deploy_runtime.execute_deploy(migrated, aid2, registry=registry_with(w), worker_kind=w.kind)
    assert deploy_runtime.verify_deploy(migrated, aid2, actual_cost=0).verified is False
    # the prior verified deployment remains the canonical latest verified deployment
    latest = deploy_state.latest_verified_deployment(migrated, r.s.venture_id, r.s.target_id)
    assert latest["action_request_id"] == str(r.s.deploy_action_id)
    assert _lifecycle(migrated, r.s.venture_id) == "OPERATING"


def test_AB_no_deployment_record_latest_reconstructable(migrated):
    r = run_deploy(migrated, "eAB")
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.deployment_record'), to_regclass('public.current_release')")
        assert cur.fetchone() == (None, None)
    dep = deploy_state.verified_deployment(migrated, r.s.deploy_action_id)
    assert dep["deployment_target_id"] == str(r.s.target_id)


# ---- Consolidated release-authority regression + ambiguity + neutrality -------------
def test_generic_factory_deploy_bypass_impossible(migrated):
    s = setup_deploy(migrated, "eByp")  # deploy action, no release_candidate
    with pytest.raises(DeployAuthorityError):
        spec_mod.create_execution_spec(migrated, s.deploy_action_id, worker_kind="deploy-a",
                                       verifier_kind="deployment-release", timeout_seconds=60, max_attempts=1,
                                       capability_scope=["DEPLOY_CANDIDATE"], task_payload={"free": "deploy anything"})
    # a non-deploy Gate-4 action is unaffected
    vid, aid, sp = spec_action(migrated, "eByp2", verifier_kind="structured-contract",
                               expected_output_contract={"require": {"status": "done"}})
    res = factory_runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(structured_output={"status": "done"})))
    assert res.dispatched is True


@pytest.mark.parametrize("slug,kw", [
    ("eqT", dict(candidate_files=[{"path": "readme.txt", "content": "x"}])),   # Technical FAIL
    ("eqP", dict(product_manifest=_mf(features=["approval_tracking"]))),        # Product FAIL
    ("eqE", dict(product_manifest=_mf(dead_ends=["x"]))),                       # Experience FAIL
    ("eqC", dict(product_manifest=_mf(buyer="other"))),                          # Commercial FAIL
    ("eqA", dict(product_manifest=_mf(features=["dashboard"], differentiators_implemented=[]))),  # AntiGeneric FAIL
])
def test_quality_prerequisite_blocks_release(migrated, slug, kw):
    r = full_eval(migrated, slug, key=slug, **kw)
    t = target_mod.register_deployment_target(migrated, r.auth.venture_id, environment="staging",
                                              provider_kind="fake-a", target_ref=f"deploy://{slug}/s")
    aid = deploy_action(migrated, r.auth.venture_id, key=slug)
    with pytest.raises(DeployAuthorityError):
        release_mod.create_release_candidate(migrated, aid, build_manifest_id=r.manifest_id,
                                             deployment_target_id=t.deployment_target_id)


def test_ambiguous_worker_claim_target_state_decides(migrated):
    # worker reports ambiguous/failure-ish but wrote the exact release; verifier reads the
    # actual target and VERIFIES from observed state, not the claim.
    r = run_deploy(migrated, "eAmb", mode="compliant",
                   structured_output={"deployed": False, "ambiguous": True, "verified": False})
    assert r.verify.verified is True
    assert deploy_state.promote_verified_deployment(migrated, r.s.deploy_action_id)["state"] == "OPERATING"


def test_provider_neutral_two_adapters(migrated):
    a = run_deploy(migrated, "eNa", provider="fake-a", key="eNa")
    assert a.verify.verified is True
    b = run_deploy(migrated, "eNb", provider="fake-b", key="eNb", worker_cls=DeployBundleWorkerB)
    assert b.verify.verified is True
