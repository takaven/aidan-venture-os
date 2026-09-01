"""End-to-end Fly deploy chain (DB; no Fly calls) — governed deploy -> independent verify -> promote.

Covers matrix rows B (prepare guard), N (VERIFIED end-to-end), O (proof-gated promotion), P (kill
blocks promotion), R (cross-venture rejected), effect semantics (FAILED / RECOVERY_REQUIRED), the
capital ledger, secret isolation (Q), and governance-delta 0 — all through the real Gate-5/6 chain
with a fake Fly transport. The local Gate-6 path (S) is proven unchanged by the existing deploy
suites; here we only add the external-provider behaviour.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from aidan_core import budget, execution, killswitch
from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import state as deploy_state
from aidan_core.deploy.fly_live_smoke import CEILING, TOKEN_ENV, run_fly_deploy_smoke
from aidan_core.deploy.fly_transport import PHASE_POST_SEND, FlyResponse, FlyTransportError
from aidan_core.deploy.fly_worker import _machine_name
from aidan_core.errors import DeployAuthorityError, ExecutionBlockedError

from deploy_fakes import deploy_action, setup_deploy, to_building

DIGEST = "sha256:" + "ab" * 32
APP = "aidan-smoke-app"
IMAGE = f"registry.fly.io/aidan-smoke@{DIGEST}"
FLY_RC = {"expected_artifact_identity": {"kind": "oci-image-digest", "digest": DIGEST},
          "image_ref": IMAGE, "region": "ams", "required_state": "started",
          "health_contract": {"marker_content": "ok"}}


class FakeFly:
    """Serves both the worker (POST create) and the observer (GET machine). ``post`` / ``get`` are
    FlyResponse or a FlyTransportError to raise; ``list_resp`` handles reconcile GET /machines."""

    def __init__(self, *, post=None, get=None, list_resp=None):
        self.post, self.get, self.list_resp = post, get, list_resp
        self.calls = []

    def __call__(self, method, path, *, token, body=None, timeout=None):
        self.calls.append((method, path))
        if method == "POST" and path.endswith("/machines"):
            if isinstance(self.post, Exception):
                raise self.post
            return self.post
        if method == "GET" and path.endswith("/machines"):        # list (reconcile)
            if isinstance(self.list_resp, Exception):
                raise self.list_resp
            return self.list_resp if self.list_resp is not None else FlyResponse(200, {"machines": []})
        if method == "GET":                                        # get one machine (observer)
            if isinstance(self.get, Exception):
                raise self.get
            return self.get if self.get is not None else FlyResponse(404, {})
        return FlyResponse(404, {})


def _machine(aid, **over):
    m = {"id": "m-1", "instance_id": "i-1", "state": "started", "image_ref": {"digest": DIGEST},
         "name": _machine_name(aid)}
    m.update(over)
    return m


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "fly-secret-not-real-XYZ")


def _fly_release(conn, slug, *, rc=FLY_RC, amount=CEILING, grant=Decimal("1.00")):
    s = setup_deploy(conn, slug, provider_kind="fly-machines", target_ref=APP)
    if grant:
        budget.grant_budget(conn, s.venture_id, amount=grant, currency="USD")
    aid = deploy_action(conn, s.venture_id, key=f"{slug}-fly", amount=amount)
    release_mod.create_release_candidate(
        conn, aid, build_manifest_id=s.build_manifest_id, deployment_target_id=s.target_id,
        release_contract=rc)
    to_building(conn, s.venture_id)
    return s, aid


# ---- N + O: VERIFIED end-to-end -> proof -> OPERATING ---------------------------------
def test_N_O_verified_deploy_promotes_operating(migrated):
    s, aid = _fly_release(migrated, "fly-ok")
    fake = FakeFly(post=FlyResponse(201, _machine(aid)), get=FlyResponse(200, _machine(aid)))
    ev = run_fly_deploy_smoke(migrated, aid, transport=fake, health_probe=lambda: "ok")
    assert ev["result"] == "PASS" and ev["deployment_verdict"] == "VERIFIED"
    assert ev["provider_contact_evidence"] == "OBSERVED" and ev["deployment_effect"] == "OBSERVED"
    assert ev["proof_verification_type"] == "DEPLOYMENT_RELEASE" and ev["proof_result"] == "VERIFIED"
    assert ev["final_status"] == "SUCCEEDED" and ev["lifecycle_state"] == "OPERATING"
    assert ev["governance_deltas"] == 0 and ev["secret_leak_check"] == "PASS"
    assert ev["machine_id"] == "m-1"
    # capital: reservation reconciled, conservative ceiling committed, nothing left reserved
    assert ev["reserved"] == "0.0000" and ev["committed"] == "0.0500"


# ---- Q: no credential anywhere in the sanitized evidence -----------------------------
def test_Q_no_secret_in_evidence(migrated):
    s, aid = _fly_release(migrated, "fly-secret")
    fake = FakeFly(post=FlyResponse(201, _machine(aid)), get=FlyResponse(200, _machine(aid)))
    ev = run_fly_deploy_smoke(migrated, aid, transport=fake, health_probe=lambda: "ok")
    assert "fly-secret-not-real-XYZ" not in json.dumps(ev) and ev["secret_leak_check"] == "PASS"


# ---- wrong observed digest -> REJECTED, no OPERATING ---------------------------------
def test_wrong_digest_rejected_no_operating(migrated):
    s, aid = _fly_release(migrated, "fly-baddigest")
    other = _machine(aid, image_ref={"digest": "sha256:" + "cd" * 32})
    fake = FakeFly(post=FlyResponse(201, _machine(aid)), get=FlyResponse(200, other))
    ev = run_fly_deploy_smoke(migrated, aid, transport=fake, health_probe=lambda: "ok")
    assert ev["deployment_verdict"] == "REJECTED" and ev["result"] == "FAIL"
    assert ev["lifecycle_state"] != "OPERATING"


# ---- effect semantics: definitive rejection -> FAILED (no effect, released) ----------
def test_definitive_rejection_failed_released(migrated):
    s, aid = _fly_release(migrated, "fly-reject")
    fake = FakeFly(post=FlyResponse(422, {}))
    ev = run_fly_deploy_smoke(migrated, aid, transport=fake)
    assert ev["result"] == "FAIL" and ev["deployment_effect"] == "NOT_OBSERVED"
    assert execution.get_status(migrated, aid) == "FAILED"
    assert ev["committed"] == "0.0000" and ev["reserved"] == "0.0000"    # no effect -> released


# ---- effect semantics: ambiguous create -> RECOVERY_REQUIRED (held, no promote) ------
def test_ambiguous_effect_recovery_required(migrated):
    s, aid = _fly_release(migrated, "fly-ambig")
    fake = FakeFly(post=FlyTransportError("t", phase=PHASE_POST_SEND),
                   list_resp=FlyTransportError("t", phase=PHASE_POST_SEND))
    ev = run_fly_deploy_smoke(migrated, aid, transport=fake)
    assert ev["result"] == "RECOVERY_REQUIRED" and ev["deployment_effect"] == "UNKNOWN"
    assert execution.get_status(migrated, aid) == "RECOVERY_REQUIRED"
    assert ev["lifecycle_state"] != "OPERATING"


# ---- B: prepare guard — a fly release with NO frozen digest cannot be prepared -------
def test_B_no_frozen_digest_blocks_prepare(migrated):
    rc = {"health_contract": {"marker_content": "ok"}}     # no expected_artifact_identity
    s, aid = _fly_release(migrated, "fly-nodigest", rc=rc)
    with pytest.raises(DeployAuthorityError):
        deploy_runtime.prepare_deploy_execution(migrated, aid, worker_kind="fly-machines")


# ---- P: kill switch engaged after the proof blocks OPERATING -------------------------
def test_P_kill_blocks_promotion(migrated):
    s, aid = _fly_release(migrated, "fly-kill")
    fake = FakeFly(post=FlyResponse(201, _machine(aid)), get=FlyResponse(200, _machine(aid)))
    # Verify (produce the proof) WITHOUT promoting: drive worker + verify directly.
    from aidan_core.factory.workers import WorkerRegistry
    from aidan_core.deploy.fly_worker import FlyMachinesWorker
    from aidan_core.deploy.observe import FlyDeploymentObserver
    reg = WorkerRegistry(); reg.register(FlyMachinesWorker(transport=fake))
    deploy_runtime.execute_deploy(migrated, aid, registry=reg, worker_kind="fly-machines",
                                  timeout_seconds=120, max_attempts=1)

    def _of(contract):
        return FlyDeploymentObserver(fly_transport=fake, token="t", app=APP, machine_id="m-1",
                                     venture_id=contract.get("venture_id"),
                                     deployment_target_id=contract.get("deployment_target_id"),
                                     health_probe=lambda: "ok", required_state="started")
    out = deploy_runtime.verify_deploy(migrated, aid, actual_cost=CEILING, observer_factory=_of)
    assert out.verified is True
    killswitch.engage_global(migrated, engaged_by="op", reason="halt")
    with pytest.raises(ExecutionBlockedError):
        deploy_state.promote_verified_deployment(migrated, aid)
    with migrated.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (s.venture_id,))
        assert cur.fetchone()[0] == "BUILDING"


# ---- R: cross-venture target binding rejected ----------------------------------------
def test_R_cross_venture_target_rejected(migrated):
    sA = setup_deploy(migrated, "fly-vA", provider_kind="fly-machines", target_ref=APP)
    sB = setup_deploy(migrated, "fly-vB", provider_kind="fly-machines", target_ref="other-app",
                      environment="prod")
    aidB = deploy_action(migrated, sB.venture_id, key="vB-fly", amount=CEILING)
    # venture B's deploy action binding venture A's target -> rejected.
    with pytest.raises(DeployAuthorityError):
        release_mod.create_release_candidate(
            migrated, aidB, build_manifest_id=sB.build_manifest_id,
            deployment_target_id=sA.target_id, release_contract=FLY_RC)
