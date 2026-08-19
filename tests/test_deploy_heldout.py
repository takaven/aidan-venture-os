"""Gate 6 / Slice 4 — HELD-OUT deployment/recovery evals.

Separate file, distinct data (alternate adapters, environments, release contracts,
candidate content, failure modes) driving the SAME frozen Gate-6 production runtime.
No production special-casing; expected outcomes live only in assertions.
"""
from __future__ import annotations

import os

import pytest

from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import state as deploy_state
from aidan_core.deploy.runtime import deploy_target_path
from aidan_core.errors import DeployAuthorityError
from factory_fakes import registry_with

from deploy_fakes import (
    DeployBundleWorker,
    DeployBundleWorkerB,
    deploy_action,
    run_deploy,
    setup_deploy,
    to_building,
)


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def test_H1_alternate_adapter_exact_deployment_operating(migrated):
    r = run_deploy(migrated, "hd1", provider="edge-runtime", environment="canary",
                   worker_cls=DeployBundleWorkerB)
    assert r.verify.verified is True
    assert deploy_state.promote_verified_deployment(migrated, r.s.deploy_action_id)["state"] == "OPERATING"


def test_H2_deceptively_healthy_wrong_revision_fails(migrated):
    r = run_deploy(migrated, "hd2", provider="containerd", environment="prod",
                   mode="wrong_bytes", structured_output={"deployed": True, "health": "green"})
    assert r.verify.verified is False
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, r.s.deploy_action_id)
    assert _lifecycle(migrated, r.s.venture_id) == "BUILDING"


def test_H3_exact_release_runtime_contract_failure(migrated):
    r = run_deploy(migrated, "hd3", provider="wasm-edge", environment="preview",
                   mode="compliant", release_contract={"runtime_kind": "wasm", "entry_artifact": "dist/app.wasm"})
    assert r.verify.verified is False  # exact bytes + health, but required entry artifact absent


def test_H4_ambiguous_outcome_exact_target_state_decides(migrated):
    # alternate adapter reports an ambiguous/failed outcome, but the target independently
    # contains the exact valid release -> observed target state decides -> VERIFIED.
    r = run_deploy(migrated, "hd4", provider="serverless-x", environment="canary",
                   worker_cls=DeployBundleWorkerB, mode="compliant",
                   structured_output={"outcome": "timeout", "deployed": "unknown"})
    assert r.verify.verified is True
    assert deploy_state.promote_verified_deployment(migrated, r.s.deploy_action_id)["state"] == "OPERATING"


def test_H5_retry_corrected_target_operating(migrated):
    s = setup_deploy(migrated, "hd5", provider_kind="fleet", environment="prod",
                     target_ref="deploy://hd5/prod")
    rc = release_mod.create_release_candidate(migrated, s.deploy_action_id,
                                              build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id)
    to_building(migrated, s.venture_id)
    deploy_runtime.execute_deploy(migrated, s.deploy_action_id,
                                  registry=registry_with(DeployBundleWorker(mode="nothing")),
                                  worker_kind="deploy-a", max_attempts=2)
    assert deploy_runtime.verify_deploy(migrated, s.deploy_action_id, actual_cost=0).verified is False
    deploy_runtime.execute_deploy(migrated, s.deploy_action_id,
                                  registry=registry_with(DeployBundleWorker(mode="compliant")),
                                  worker_kind="deploy-a", max_attempts=2)
    assert deploy_runtime.verify_deploy(migrated, s.deploy_action_id, actual_cost=0).verified is True
    assert deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)["state"] == "OPERATING"
    assert release_mod.get_release_candidate(migrated, s.deploy_action_id)[8] == rc.release_hash
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (s.deploy_action_id,))
        assert cur.fetchone()[0] == 2


def test_H6_cross_venture_identical_artifact_rejected(migrated):
    a = setup_deploy(migrated, "hd6a", key="hd6a", provider_kind="fleet")
    b = setup_deploy(migrated, "hd6b", key="hd6b", provider_kind="fleet")
    # venture A's deploy action cannot bind venture B's target even for identical builds
    with pytest.raises(DeployAuthorityError):
        release_mod.create_release_candidate(migrated, a.deploy_action_id,
                                             build_manifest_id=a.build_manifest_id, deployment_target_id=b.target_id)


def test_H7_prior_verified_survives_later_failure(migrated):
    r = run_deploy(migrated, "hd7", provider="fleet", environment="prod")
    deploy_state.promote_verified_deployment(migrated, r.s.deploy_action_id)
    aid2 = deploy_action(migrated, r.s.venture_id, key="hd7b")
    release_mod.create_release_candidate(migrated, aid2, build_manifest_id=r.s.build_manifest_id,
                                         deployment_target_id=r.s.target_id)
    w = DeployBundleWorker(mode="wrong_bytes")
    deploy_runtime.execute_deploy(migrated, aid2, registry=registry_with(w), worker_kind=w.kind)
    assert deploy_runtime.verify_deploy(migrated, aid2, actual_cost=0).verified is False
    latest = deploy_state.latest_verified_deployment(migrated, r.s.venture_id, r.s.target_id)
    assert latest["action_request_id"] == str(r.s.deploy_action_id)
    assert _lifecycle(migrated, r.s.venture_id) == "OPERATING"


def test_H8_forged_generic_proof_and_lifecycle_claim_rejected(migrated):
    # a deploy whose worker forges deployed/lifecycle but writes nothing -> no deployment
    # proof; promotion rejected; lifecycle stays BUILDING.
    r = run_deploy(migrated, "hd8", provider="fleet", mode="nothing",
                   structured_output={"deployed": True, "lifecycle": "OPERATING", "verified": True})
    assert r.verify.verified is False
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, r.s.deploy_action_id)
    assert _lifecycle(migrated, r.s.venture_id) == "BUILDING"
