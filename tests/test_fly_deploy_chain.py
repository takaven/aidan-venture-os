"""End-to-end Stage-C Fly deploy boundary smoke (DB; no Fly calls) — self-contained fixture ->
governed create -> independent verify -> proof -> governed cleanup, NO OPERATING promotion.

Covers matrix rows A (fresh migrated DB self-establishes the whole fixture), B (no foreign
ActionRequest dependency), E (no source->artifact derivation claim), K (proof only after read-back),
L (lifecycle stays BUILDING), N (cleanup confirms absence), O (ambiguous cleanup is not a clean
PASS), P (failed verification does not promote), Q (no secret), plus RECOVERY_REQUIRED semantics.
Worker/observer/cleanup unit rows (C,D,F,G,H,I,J,M,R,S) live in the DB-free suites; the local
Gate-6 path (T) and Codex boundary (U) are proven unchanged by their existing suites.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from aidan_core import execution
from aidan_core.deploy.fly_live_smoke import CEILING, TOKEN_ENV, run_fly_deploy_smoke
from aidan_core.deploy.fly_transport import PHASE_POST_SEND, FlyResponse, FlyTransportError

DIGEST = "sha256:" + "ab" * 32
APP = "aidan-smoke-app"
IMAGE = f"registry.fly.io/aidan-smoke@{DIGEST}"


class StatefulFly:
    """Serves worker POST create, observer GET (200 running), and cleanup DELETE+GET (404 after
    delete). ``digest`` controls the observed image digest; ``get_after_delete_raises`` makes the
    post-delete confirmation ambiguous."""

    def __init__(self, *, digest=DIGEST, state="started", post=None,
                 get_after_delete_raises=False):
        self.digest, self.state, self.post_override = digest, state, post
        self.get_after_delete_raises = get_after_delete_raises
        self.deleted = False
        self.calls = []

    def _machine(self):
        return {"id": "m-1", "instance_id": "i-1", "state": self.state,
                "image_ref": {"digest": self.digest}, "name": "aidan-x"}

    def __call__(self, method, path, *, token, body=None, timeout=None):
        self.calls.append((method, path))
        if method == "POST" and path.endswith("/machines"):
            if isinstance(self.post_override, Exception):
                raise self.post_override
            return self.post_override or FlyResponse(201, self._machine())
        if method == "GET" and path.endswith("/machines"):
            return FlyResponse(200, {"machines": []})
        if method == "DELETE":
            self.deleted = True
            return FlyResponse(200, {"ok": True})
        if method == "GET":                      # get one machine
            if self.deleted:
                if self.get_after_delete_raises:
                    raise FlyTransportError("t", phase=PHASE_POST_SEND)
                return FlyResponse(404, {})
            return FlyResponse(200, self._machine())
        return FlyResponse(404, {})

    def count(self, method):
        return sum(1 for c in self.calls if c[0] == method)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "fly-secret-not-real-XYZ")


def _run(conn, fake, *, slug, **kw):
    return run_fly_deploy_smoke(conn, app=APP, image_ref=IMAGE, internal_port=80, health_path="/",
                                transport=fake, health_probe=lambda: "ok", slug=slug, **kw)


# ---- A + L + N + K + E: clean end-to-end boundary smoke, BUILDING, cleanup confirmed --
def test_A_self_contained_pass_stays_building(migrated):
    fake = StatefulFly()
    ev = _run(migrated, fake, slug="fly-a")
    assert ev["result"] == "PASS" and ev["deployment_verdict"] == "VERIFIED"
    assert ev["proof_verification_type"] == "DEPLOYMENT_RELEASE" and ev["proof_result"] == "VERIFIED"
    assert ev["deployment_effect"] == "OBSERVED" and ev["provider_contact_evidence"] == "OBSERVED"
    # L: lifecycle deliberately stays BUILDING; NOT promoted to OPERATING.
    assert ev["lifecycle_state"] == "BUILDING" and ev["promoted_to_operating"] is False
    assert ev["final_status"] == "SUCCEEDED"
    # N: cleanup deleted the machine and independently confirmed absence.
    assert ev["cleanup_state"] == "CLEANUP_CONFIRMED" and fake.count("DELETE") == 1
    # E: no source->artifact derivation is claimed.
    assert ev["source_to_artifact_derivation_proven"] is False
    # A/B: the fixture created its own canonical ids in THIS db.
    ids = ev["canonical_ids"]
    assert ids["venture_id"] and ids["deploy_action_id"] and ids["release_candidate_id"]
    assert ev["governance_deltas"] == 0 and ev["secret_leak_check"] == "PASS"
    # capital: reserved back to 0, conservative ceiling committed, released reported
    assert ev["reserved"] == "0.0000" and ev["committed"] == "0.0500" and ev["released"] == "0.0000"


# ---- Q: credential never appears in evidence -----------------------------------------
def test_Q_no_secret_in_evidence(migrated):
    ev = _run(migrated, StatefulFly(), slug="fly-secret")
    assert "fly-secret-not-real-XYZ" not in json.dumps(ev) and ev["secret_leak_check"] == "PASS"


# ---- P: failed verification (wrong digest) -> FAIL, no promote, no proof, still cleaned up
def test_P_wrong_digest_fails_no_promote(migrated):
    fake = StatefulFly(digest="sha256:" + "cd" * 32)
    ev = _run(migrated, fake, slug="fly-baddigest")
    assert ev["deployment_verdict"] == "REJECTED" and ev["result"] == "FAIL"
    assert ev["lifecycle_state"] == "BUILDING" and ev.get("proof_result") != "VERIFIED"
    assert fake.count("DELETE") == 1                       # a created machine is still torn down


# ---- O: verified boundary but ambiguous cleanup is NOT a clean PASS -------------------
def test_O_ambiguous_cleanup_not_clean_pass(migrated):
    fake = StatefulFly(get_after_delete_raises=True)
    ev = _run(migrated, fake, slug="fly-ambigclean")
    assert ev["deployment_verdict"] == "VERIFIED"
    assert ev["cleanup_state"] == "CLEANUP_AMBIGUOUS" and ev["result"] == "PASS_UNCLEAN_CLEANUP"
    assert "owner_reconciliation" in ev and ev["lifecycle_state"] == "BUILDING"


# ---- RECOVERY_REQUIRED: ambiguous create -> held, no promote, no machine kept ---------
def test_ambiguous_create_recovery_required(migrated):
    fake = StatefulFly(post=FlyTransportError("t", phase=PHASE_POST_SEND))
    ev = _run(migrated, fake, slug="fly-ambigcreate")
    assert ev["result"] == "RECOVERY_REQUIRED"
    assert execution.get_status(migrated, ev["action_request_id"]) == "RECOVERY_REQUIRED"
    assert ev["lifecycle_state"] == "BUILDING"


# ---- definitive rejection -> FAIL, no effect, released -------------------------------
def test_definitive_rejection_failed(migrated):
    fake = StatefulFly(post=FlyResponse(422, {}))
    ev = _run(migrated, fake, slug="fly-reject")
    assert ev["result"] == "FAIL" and ev["deployment_effect"] == "NOT_OBSERVED"
    assert execution.get_status(migrated, ev["action_request_id"]) == "FAILED"
    assert ev["committed"] == "0.0000"                    # no effect -> released
