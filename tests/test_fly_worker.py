"""Deterministic Fly Machines WorkerAdapter matrix — NO Fly calls (fake transport only).

Covers matrix rows A,C,D,E,F,G,H,I (+ digest-guard B-worker, + secret Q) by driving the real
``FlyMachinesWorker`` against a fake transport: no network, no provider mutation. The worker holds
no DB and self-certifies nothing; these prove its guards, exact-target/exact-digest behaviour,
failure taxonomy, and deterministic reconciliation.
"""
from __future__ import annotations

import json

import pytest

from aidan_core.errors import AmbiguousExternalEffectError, DeployAdapterError
from aidan_core.deploy.fly_transport import PHASE_POST_SEND, PHASE_PRE_SEND, FlyResponse, FlyTransportError
from aidan_core.deploy.fly_worker import TOKEN_ENV, FlyMachinesWorker, _machine_name, cleanup_machine
from aidan_core.factory.workers import WorkerRequest

DIGEST = "sha256:" + "ab" * 32
APP = "aidan-smoke-app"
IMAGE = f"registry.fly.io/aidan-smoke@{DIGEST}"


class FakeFly:
    """Records calls; returns programmed FlyResponse or raises a programmed FlyTransportError,
    matched by (method, path-substring). Default: 404."""

    def __init__(self, responses=None, raises=None):
        self.calls = []
        self._responses = responses or []      # list of (method, substr, FlyResponse)
        self._raises = raises or []            # list of (method, substr, FlyTransportError)

    def __call__(self, method, path, *, token, body=None, timeout=None):
        self.calls.append({"method": method, "path": path, "token": token, "body": body})
        for m, sub, err in self._raises:
            if method == m and sub in path:
                raise err
        for m, sub, resp in self._responses:
            if method == m and sub in path:
                return resp
        return FlyResponse(404, {})

    def count(self, method):
        return sum(1 for c in self.calls if c["method"] == method)


RUNTIME_CONTRACT = {"internal_port": 8080, "protocol": "tcp",
                    "ports": [{"port": 80, "handlers": ["http"]}, {"port": 443, "handlers": ["tls", "http"]}]}


def _request(*, image=IMAGE, digest=DIGEST, provider="fly-machines", app=APP, aid="a1b2c3d4-0000",
             runtime_contract=RUNTIME_CONTRACT):
    rc = {"expected_artifact_identity": {"kind": "oci-image-digest", "digest": digest} if digest else None,
          "image_ref": image, "region": "ams", "required_state": "started",
          "runtime_contract": runtime_contract, "health_contract": {"path": "/"}}
    if digest is None:
        rc.pop("expected_artifact_identity")
    if runtime_contract is None:
        rc.pop("runtime_contract")
    return WorkerRequest(
        action_request_id=aid, attempt_id="att-1", venture_id="ven-1", spec_hash="sh",
        worker_kind="fly-machines", capabilities=("DEPLOY_CANDIDATE",), timeout_seconds=60,
        workspace_ref="deploy://x", declared_inputs={},
        task_payload={"deploy": {"provider_kind": provider, "target_ref": app,
                                 "deployment_target_id": "tgt-1", "release_contract": rc}},
        expected_output_contract={})


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "fly-secret-not-real-XYZ")


def _created(**over):
    m = {"id": "m-123", "instance_id": "i-123", "state": "started",
         "image_ref": {"digest": DIGEST}, "name": _machine_name("a1b2c3d4-0000")}
    m.update(over)
    return m


# ---- A: missing credential -> no mutation ---------------------------------------------
def test_A_missing_token_fails_before_mutation(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    fake = FakeFly()
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request())
    assert ei.value.code == "FLY_AUTH_MISSING" and fake.calls == []


# ---- B(worker): missing frozen digest -> no mutation ----------------------------------
def test_B_missing_frozen_digest_no_dispatch():
    fake = FakeFly()
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request(digest=None))
    assert ei.value.code == "FLY_ARTIFACT_DIGEST_MISSING" and fake.calls == []


# ---- C: worker attempts the EXACT frozen target only ----------------------------------
def test_C_targets_exact_frozen_app():
    fake = FakeFly(responses=[("POST", "/machines", FlyResponse(200, _created()))])
    FlyMachinesWorker(transport=fake).execute(_request())
    post = next(c for c in fake.calls if c["method"] == "POST")
    assert post["path"] == f"/apps/{APP}/machines"
    assert post["body"]["name"] == _machine_name("a1b2c3d4-0000")


# ---- D: worker deploys the EXACT frozen digest only -----------------------------------
def test_D_deploys_exact_frozen_digest():
    fake = FakeFly(responses=[("POST", "/machines", FlyResponse(200, _created()))])
    FlyMachinesWorker(transport=fake).execute(_request())
    post = next(c for c in fake.calls if c["method"] == "POST")
    assert post["body"]["config"]["image"] == IMAGE            # exactly the frozen digest-pinned ref


# ---- F: the exact frozen Machine service/port config is emitted (worker invents nothing) ----------
def test_F_emits_frozen_service_config():
    fake = FakeFly(responses=[("POST", "/machines", FlyResponse(200, _created()))])
    FlyMachinesWorker(transport=fake).execute(_request())
    cfg = next(c for c in fake.calls if c["method"] == "POST")["body"]["config"]
    assert cfg["services"] == [{"protocol": "tcp", "internal_port": 8080,
                                "ports": RUNTIME_CONTRACT["ports"]}]
    assert cfg["checks"]["http"]["port"] == 8080 and cfg["checks"]["http"]["path"] == "/"


# ---- G: missing required networking/runtime config -> NO create -----------------------
def test_G_missing_runtime_contract_no_create():
    fake = FakeFly()
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request(runtime_contract=None))
    assert ei.value.code == "FLY_RUNTIME_CONTRACT_MISSING" and fake.calls == []


def test_D_image_digest_mismatch_rejected_before_mutation():
    other = "sha256:" + "cd" * 32
    fake = FakeFly()
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request(image=f"reg/x@{other}"))
    assert ei.value.code == "FLY_IMAGE_DIGEST_MISMATCH" and fake.calls == []


# ---- E: definitive 4xx rejection -> FAILED (no reconcile) -----------------------------
def test_E_definitive_rejection_fails():
    fake = FakeFly(responses=[("POST", "/machines", FlyResponse(422, {}))])
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request())
    assert ei.value.code == "FLY_CREATE_REJECTED_422"
    assert fake.count("POST") == 1 and fake.count("GET") == 0   # no reconcile after a definitive 4xx


# ---- F: timeout BEFORE transmission -> known no-effect failure ------------------------
def test_F_pre_send_timeout_is_no_effect():
    fake = FakeFly(raises=[("POST", "/machines", FlyTransportError("t", phase=PHASE_PRE_SEND))])
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request())
    assert ei.value.code == "FLY_CREATE_NOT_SENT" and fake.count("GET") == 0   # no reconcile


# ---- G/I: timeout AFTER transmission -> reconcile; unresolved -> RECOVERY_REQUIRED -----
def test_G_post_send_then_reconcile_unreachable_is_ambiguous():
    fake = FakeFly(raises=[("POST", "/machines", FlyTransportError("t", phase=PHASE_POST_SEND)),
                           ("GET", "/machines", FlyTransportError("t", phase=PHASE_POST_SEND))])
    with pytest.raises(AmbiguousExternalEffectError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request())
    assert "FLY_RECONCILE_UNREACHABLE" in str(ei.value)


def test_I_post_send_reconcile_inconclusive_is_recovery_required():
    fake = FakeFly(raises=[("POST", "/machines", FlyTransportError("t", phase=PHASE_POST_SEND))],
                   responses=[("GET", "/machines", FlyResponse(500, {}))])
    with pytest.raises(AmbiguousExternalEffectError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request())
    assert "FLY_RECONCILE_INCONCLUSIVE" in str(ei.value)


def test_proven_absence_after_reconcile_is_failed_no_effect():
    # list read OK, our named machine absent -> the create had no effect -> FAILED (released).
    fake = FakeFly(raises=[("POST", "/machines", FlyTransportError("t", phase=PHASE_POST_SEND))],
                   responses=[("GET", "/machines", FlyResponse(200, {"machines": []}))])
    with pytest.raises(DeployAdapterError) as ei:
        FlyMachinesWorker(transport=fake).execute(_request())
    assert ei.value.code == "FLY_CREATE_NO_EFFECT"


# ---- H: reconcile finds the exact machine -> no duplicate create ----------------------
def test_H_reconcile_finds_machine_no_duplicate():
    machine = _created(id="m-recon")
    fake = FakeFly(raises=[("POST", "/machines", FlyTransportError("t", phase=PHASE_POST_SEND))],
                   responses=[("GET", "/machines", FlyResponse(200, {"machines": [machine]}))])
    result = FlyMachinesWorker(transport=fake).execute(_request())
    assert result.structured_output["machine_id"] == "m-recon"
    assert fake.count("POST") == 1                              # never a second create attempt


# ---- happy path claim is inert + carries the durable identity -------------------------
def test_success_returns_inert_claim_with_durable_identity():
    fake = FakeFly(responses=[("POST", "/machines", FlyResponse(201, _created()))])
    result = FlyMachinesWorker(transport=fake).execute(_request())
    assert result.external_result_id == "fly-machine:m-123"
    assert result.structured_output["machine_id"] == "m-123"
    assert result.structured_output["create_effect"] == "OBSERVED"


# ---- Q: the credential never appears in the worker's result --------------------------
def test_Q_no_secret_in_worker_result():
    fake = FakeFly(responses=[("POST", "/machines", FlyResponse(200, _created()))])
    result = FlyMachinesWorker(transport=fake).execute(_request())
    blob = json.dumps(result.structured_output) + result.external_result_id + result.reported_outcome
    assert "fly-secret-not-real-XYZ" not in blob


# ---- M/N/O: governed cleanup deletes only the exact machine + independently confirms absence ------
def test_M_N_cleanup_deletes_exact_machine_and_confirms_absence():
    fake = FakeFly(responses=[("DELETE", "/machines/m-1", FlyResponse(200, {"ok": True})),
                              ("GET", "/machines/m-1", FlyResponse(404, {}))])
    state = cleanup_machine(fake, "tok", APP, "m-1")
    assert state == "CLEANUP_CONFIRMED"
    dele = next(c for c in fake.calls if c["method"] == "DELETE")
    assert dele["path"] == f"/apps/{APP}/machines/m-1?force=true"   # exactly that machine, force


def test_O_cleanup_still_present_is_failed():
    fake = FakeFly(responses=[("DELETE", "/machines/m-1", FlyResponse(200, {"ok": True})),
                              ("GET", "/machines/m-1", FlyResponse(200, {"id": "m-1", "state": "started"}))])
    assert cleanup_machine(fake, "tok", APP, "m-1") == "CLEANUP_FAILED"


def test_O_cleanup_unreachable_confirm_is_ambiguous_no_retry():
    fake = FakeFly(raises=[("GET", "/machines/m-1", FlyTransportError("t", phase=PHASE_POST_SEND))],
                   responses=[("DELETE", "/machines/m-1", FlyResponse(200, {"ok": True}))])
    assert cleanup_machine(fake, "tok", APP, "m-1") == "CLEANUP_AMBIGUOUS"
    assert fake.count("DELETE") == 1                                # never re-issued the DELETE


# ---- C/D/E: artifact identity frozen; digest changes release identity; no derivation claim -------
def test_C_D_E_artifact_identity_frozen_and_honest():
    from aidan_core.deploy import artifact as artifact_mod
    from aidan_core.deploy.fly_smoke_fixture import build_release_contract
    rc = build_release_contract(image_ref=IMAGE, internal_port=80, health_path="/")
    # C: BOTH the full digest-pinned image_ref AND the expected_artifact_identity are frozen.
    assert rc["image_ref"] == IMAGE
    assert rc["expected_artifact_identity"]["digest"] == DIGEST
    # D: a different image digest yields a different frozen artifact identity (=> different release_hash).
    other = f"registry.fly.io/x@sha256:{'cd' * 32}"
    rc2 = build_release_contract(image_ref=other, internal_port=80, health_path="/")
    assert rc2["expected_artifact_identity"] != rc["expected_artifact_identity"]
    # E: the module records that source->artifact derivation is NOT proven by this smoke.
    assert artifact_mod.SOURCE_TO_ARTIFACT_DERIVATION_PROVEN is False
