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
from aidan_core.deploy.fly_worker import TOKEN_ENV, FlyMachinesWorker, _machine_name
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


def _request(*, image=IMAGE, digest=DIGEST, provider="fly-machines", app=APP, aid="a1b2c3d4-0000"):
    rc = {"expected_artifact_identity": {"kind": "oci-image-digest", "digest": digest} if digest else None,
          "image_ref": image, "region": "ams", "required_state": "started"}
    if digest is None:
        rc.pop("expected_artifact_identity")
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
