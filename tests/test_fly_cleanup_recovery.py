"""Fly cleanup hardening + standalone recovery + failure diagnostics (DB-free, no Fly calls).

Matrix rows: A-E (a REJECTED verification retains per-check + digest/state/health diagnostics, no
secret), F-I (one DELETE max; transient-GET->404 confirms; persistent present -> FAILED; all
inconclusive -> AMBIGUOUS), J-O (recovery: pre-404 no delete; exact machine one delete; mismatch no
delete; SHA/token guards no mutation; recovery never creates/deploys).
"""
from __future__ import annotations

import json

import pytest

from aidan_core.deploy import fly_recovery
from aidan_core.deploy.fly_live_smoke import _build_diagnostics, observed_health_from_body
from aidan_core.deploy.fly_transport import PHASE_POST_SEND, FlyResponse, FlyTransportError
from aidan_core.deploy.fly_worker import cleanup_machine
from aidan_core.deploy.observe import DeploymentObservation

DIGEST = "sha256:" + "2e" * 32
APP = "aidan-gate8-smoke-f0a8f1"
MID = "e82d1636f3d348"
NOSLEEP = lambda *_: None


class SeqFly:
    """Serves DELETE once and GETs in sequence. Records calls."""

    def __init__(self, *, delete=None, gets=None):
        self.delete = delete
        self.gets = list(gets or [])
        self.calls = []

    def __call__(self, method, path, *, token, body=None, timeout=None):
        self.calls.append((method, path))
        if method == "DELETE":
            if isinstance(self.delete, Exception):
                raise self.delete
            return self.delete or FlyResponse(200, {"ok": True})
        if method == "GET":
            i = sum(1 for c in self.calls if c[0] == "GET") - 1
            item = self.gets[min(i, len(self.gets) - 1)] if self.gets else FlyResponse(404, {})
            if isinstance(item, Exception):
                raise item
            return item
        return FlyResponse(404, {})

    def count(self, m):
        return sum(1 for c in self.calls if c[0] == m)


# ---- F/G/H/I: cleanup hardening -------------------------------------------------------
def test_F_cleanup_single_delete_max():
    f = SeqFly(gets=[FlyResponse(404, {})])
    assert cleanup_machine(f, "tok", APP, MID, sleep=NOSLEEP) == "CLEANUP_CONFIRMED"
    assert f.count("DELETE") == 1


def test_G_transient_get_then_404_confirms():
    f = SeqFly(gets=[FlyTransportError("t", phase=PHASE_POST_SEND), FlyResponse(404, {})])
    assert cleanup_machine(f, "tok", APP, MID, sleep=NOSLEEP) == "CLEANUP_CONFIRMED"
    assert f.count("DELETE") == 1                       # never a second DELETE despite the transient


def test_H_persistent_present_is_failed():
    f = SeqFly(gets=[FlyResponse(200, {"id": MID, "state": "started"})])
    assert cleanup_machine(f, "tok", APP, MID, sleep=NOSLEEP) == "CLEANUP_FAILED"
    assert f.count("DELETE") == 1


def test_I_all_inconclusive_is_ambiguous():
    f = SeqFly(gets=[FlyTransportError("t", phase=PHASE_POST_SEND)] * 4)
    assert cleanup_machine(f, "tok", APP, MID, sleep=NOSLEEP) == "CLEANUP_AMBIGUOUS"
    assert f.count("DELETE") == 1                       # exactly one DELETE, no retry


# ---- J/K/L: recovery mutation guards -------------------------------------------------
def test_J_pre_get_404_already_clean_no_delete():
    f = SeqFly(gets=[FlyResponse(404, {})])
    ev = fly_recovery.run_fly_recovery(app=APP, machine_id=MID, transport=f, token="tok", sleep=NOSLEEP)
    assert ev["result"] == "RECOVERY_ALREADY_CLEAN" and ev["delete_issued"] is False
    assert f.count("DELETE") == 0 and f.count("POST") == 0


def test_K_exact_machine_one_delete_confirmed():
    machine = {"id": MID, "state": "started", "image_ref": {"digest": DIGEST}}
    f = SeqFly(gets=[FlyResponse(200, machine), FlyResponse(404, {})])
    ev = fly_recovery.run_fly_recovery(app=APP, machine_id=MID, transport=f, token="tok", sleep=NOSLEEP)
    assert ev["result"] == "RECOVERY_CONFIRMED" and ev["delete_issued"] is True
    assert f.count("DELETE") == 1 and f.count("POST") == 0
    assert ev["pre_cleanup"]["observed_digest"] == DIGEST and ev["pre_cleanup"]["runtime_state"] == "started"


def test_L_identity_mismatch_no_delete():
    f = SeqFly(gets=[FlyResponse(200, {"id": "somethingelse", "state": "started"})])
    ev = fly_recovery.run_fly_recovery(app=APP, machine_id=MID, transport=f, token="tok", sleep=NOSLEEP)
    assert ev["result"] == "RECOVERY_IDENTITY_MISMATCH" and ev["delete_issued"] is False
    assert f.count("DELETE") == 0


def test_pre_delete_unreachable_ambiguous_no_delete():
    f = SeqFly(gets=[FlyTransportError("t", phase=PHASE_POST_SEND)])
    ev = fly_recovery.run_fly_recovery(app=APP, machine_id=MID, transport=f, token="tok", sleep=NOSLEEP)
    assert ev["result"] == "RECOVERY_AMBIGUOUS_NO_DELETE" and f.count("DELETE") == 0


# ---- M/N: guards block before any Fly mutation ---------------------------------------
def test_M_accepted_sha_mismatch_no_mutation(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", "RUN_FLY_RECOVERY_ONLY")
    monkeypatch.setenv("FLY_RECOVERY_ACCEPTED_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("DEPLOY_FLY_API_TOKEN", "tok")
    monkeypatch.setenv("FLY_RECOVERY_APP", APP)
    monkeypatch.setenv("FLY_RECOVERY_MACHINE_ID", MID)
    assert fly_recovery.main() == 3
    assert "SHA_MISMATCH" in capsys.readouterr().out


def test_N_bad_confirm_token_no_mutation(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", "nope")
    assert fly_recovery.main() == 2
    assert "CONFIRM_REQUIRED" in capsys.readouterr().out


# ---- O: recovery cannot create/deploy (structural) -----------------------------------
def test_O_recovery_has_no_create_or_deploy():
    import inspect
    src = inspect.getsource(fly_recovery)
    assert "POST" not in src and "execute_deploy" not in src and "FlyMachinesWorker" not in src


def test_machine_id_and_app_validation():
    assert fly_recovery.validate_machine_id(MID) == MID
    for bad in ("", "XYZ", "e82d1636f3d348zz-", "not hex!", "g" * 8):
        with pytest.raises(ValueError):
            fly_recovery.validate_machine_id(bad)


# ---- A-E: failure diagnostics (from a captured observation; no extra Fly call) -------
def _captured(*, digest, state, health):
    obs = DeploymentObservation(f"fly-machines/v1/t1/{APP}", (), health, target_present=True,
                                contact="OBSERVED", artifact_identity=digest, running_state=state)
    contract = {"venture_id": "v1", "deployment_target_id": "t1",
                "expected_artifact_identity": {"kind": "oci-image-digest", "digest": DIGEST},
                "release_contract": {"health_contract": {"marker_content": "Welcome to nginx!"}},
                "required_state": "started"}
    return {"observation": obs, "contract": contract}


def test_A_B_C_D_rejected_run_retains_full_diagnostics():
    # Wrong digest + not-started + no health marker: every failing invariant is retained.
    cap = _captured(digest="sha256:" + "cd" * 32, state="created", health=None)
    diag = _build_diagnostics(cap, {"reached": True, "http_status": 200}, "Welcome to nginx!")
    names = {c["name"]: c["result"] for c in diag["checks"]}
    assert names["ARTIFACT_IDENTITY"] == "FAIL" and names["RUNTIME_STATE"] == "FAIL" and names["HEALTH"] == "FAIL"
    assert diag["observed_artifact_digest"] == "sha256:" + "cd" * 32          # B
    assert diag["expected_artifact_digest"] == DIGEST
    assert diag["observed_runtime_state"] == "created" and diag["required_runtime_state"] == "started"  # C
    assert diag["health"]["probe_reached"] is True and diag["health"]["marker_matched"] is False        # D


def test_diagnostics_marker_matched_when_healthy():
    cap = _captured(digest=DIGEST, state="started", health="Welcome to nginx!")
    diag = _build_diagnostics(cap, {"reached": True, "http_status": 200}, "Welcome to nginx!")
    assert all(c["result"] == "PASS" for c in diag["checks"])
    assert diag["health"]["marker_matched"] is True


def test_E_no_secret_in_diagnostics():
    cap = _captured(digest=DIGEST, state="started", health="Welcome to nginx!")
    diag = _build_diagnostics(cap, {"reached": True}, "Welcome to nginx!")
    assert "DEPLOY_FLY_API_TOKEN" not in json.dumps(diag) and "Bearer" not in json.dumps(diag)
