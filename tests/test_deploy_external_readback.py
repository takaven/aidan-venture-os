"""Provider-neutral external read-back seam falsification (Gate 6 real-deploy readiness).

Gate-6 Slices 1-3 proved the deployment verification architecture against a controlled LOCAL
target directory. A genuine REAL_EXTERNAL deploy reads the deployed state back from an external
provider instead. This suite proves the verifier's decision flows entirely through the injectable,
provider-neutral ``DeploymentObserver`` seam — NOT the local filesystem and NOT the worker's
self-report — by driving the exact five-check falsification through a FAKE external observer.

No network, no provider, no filesystem: the observer returns observed state as plain data. This is
the reusable engineering a real provider observer plugs into unchanged (CASE B — no provider is
selected here).
"""
from __future__ import annotations

import hashlib

import pytest

from aidan_core.deploy.observe import DeploymentObservation, ObservedFile, observed_tree_hash
from aidan_core.deploy.verifiers import DeploymentReleaseVerifier
from aidan_core.factory.verifiers import VerificationRequest

VENTURE = "venture-EXT"
TARGET = "target-EXT"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# The externally-observed "deployed" tree (no filesystem involved).
GOOD_FILES = (
    ObservedFile("app/main.py", _sha(b"print('hi')\n")),
    ObservedFile("config.json", _sha(b"{}\n")),
)
EXPECTED_HASH = observed_tree_hash(GOOD_FILES)


class FakeExternalObserver:
    """Reads deployment state back from a NON-local, NON-network source (a canned observation).
    Stands in for a real provider read-back; the verifier can tell no difference."""

    def __init__(self, observation: DeploymentObservation):
        self._obs = observation

    def observe(self) -> DeploymentObservation:
        return self._obs


def _observation(*, files=GOOD_FILES, health="ok", identity=None, contact="OBSERVED", present=True):
    return DeploymentObservation(
        isolation_identity=identity or f"provider://acct/{VENTURE}/{TARGET}",
        files=files, health_marker=health, target_present=present, contact=contact)


def _request(*, health_contract=None, entry="app/main.py"):
    contract = {
        "target_path": "/SHOULD-NOT-BE-READ",   # a real local read here would fail -> proves seam use
        "venture_id": VENTURE, "deployment_target_id": TARGET,
        "candidate_tree_hash": EXPECTED_HASH,
        "release_candidate_id": "rc-1", "release_hash": "rh-1",
        "release_contract": {"entry_artifact": entry,
                             **({"health_contract": health_contract} if health_contract else {})},
    }
    return VerificationRequest(
        action_request_id="a-1", execution_attempt_id="att-1", verifier_kind="deployment-release",
        expected_output_contract={"deployment": contract},
        # A LOUD worker self-report claiming total success — must be ignored by the verifier.
        worker_structured_output={"deployed": True, "release_verified": True, "health": "green",
                                  "overall_success": True, "lifecycle": "OPERATING"},
        artifacts=(), spec_hash="sh-1")


def _verify(observation, **req_kw):
    verifier = DeploymentReleaseVerifier(observer_factory=lambda c: FakeExternalObserver(observation))
    return verifier.verify(_request(**req_kw))


def _checks(result):
    return {c["name"]: c["result"] for c in result.detail["checks"]}


# ---- the happy external path ---------------------------------------------------------
def test_external_readback_exact_release_verifies():
    r = _verify(_observation())
    assert r.verdict == "VERIFIED" and r.verification_type == "DEPLOYMENT_RELEASE"
    assert all(v == "PASS" for v in _checks(r).values())
    assert r.detail["read_back_contact"] == "OBSERVED"


def test_local_and_external_identity_hash_are_equal():
    # The kernel identity hash is observer-independent: the SAME logical tree hashes identically
    # whether read from a local dir or an external provider read-back.
    from pathlib import Path
    import tempfile
    tp = Path(tempfile.mkdtemp()) / "release"
    for f, raw in ((("app/main.py"), b"print('hi')\n"), (("config.json"), b"{}\n")):
        p = tp / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    from aidan_core.deploy.observe import LocalTargetObserver
    local = LocalTargetObserver(str(tp.parent)).observe()
    assert observed_tree_hash(local.files) == EXPECTED_HASH


# ---- the five fail-closed cases, all via the EXTERNAL observer ------------------------
def test_external_wrong_bytes_fail_release_identity():
    bad = (ObservedFile("app/main.py", _sha(b"TAMPERED")), ObservedFile("config.json", _sha(b"{}\n")))
    r = _verify(_observation(files=bad))
    assert r.verdict == "REJECTED" and _checks(r)["RELEASE_IDENTITY"] == "FAIL"


def test_external_extra_file_changes_identity():
    extra = GOOD_FILES + (ObservedFile("app/sneaky.py", _sha(b"evil\n")),)
    r = _verify(_observation(files=extra))
    assert r.verdict == "REJECTED" and _checks(r)["RELEASE_IDENTITY"] == "FAIL"


def test_external_wrong_target_fails_isolation():
    # Read-back resolved a target NOT under this venture/target namespace.
    r = _verify(_observation(identity="provider://acct/venture-OTHER/target-OTHER"))
    assert r.verdict == "REJECTED" and _checks(r)["VENTURE_TARGET_ISOLATION"] == "FAIL"


def test_external_empty_target_fails_target_exists():
    r = _verify(_observation(files=(), present=False, contact="NOT_OBSERVED"))
    checks = _checks(r)
    assert r.verdict == "REJECTED" and checks["TARGET_EXISTS"] == "FAIL"
    assert r.detail["read_back_contact"] == "NOT_OBSERVED"


def test_external_unhealthy_fails_health():
    r = _verify(_observation(health=None))
    assert r.verdict == "REJECTED" and _checks(r)["HEALTH"] == "FAIL"


def test_external_health_marker_mismatch_fails():
    r = _verify(_observation(health="degraded"), health_contract={"marker_content": "ok"})
    assert r.verdict == "REJECTED" and _checks(r)["HEALTH"] == "FAIL"


def test_external_missing_runtime_artifact_fails_contract():
    # Exact bytes + healthy, but the required entry artifact is not among the observed files.
    r = _verify(_observation(), entry="app/DOES_NOT_EXIST.py")
    assert r.verdict == "REJECTED" and _checks(r)["REQUIRED_RUNTIME_CONTRACT"] == "FAIL"


def test_external_healthy_wrong_release_still_fails():
    # Healthy AND isolated, but wrong bytes -> still REJECTED (health is necessary, not sufficient).
    bad = (ObservedFile("app/main.py", _sha(b"WRONG")), ObservedFile("config.json", _sha(b"{}\n")))
    r = _verify(_observation(files=bad))
    c = _checks(r)
    assert r.verdict == "REJECTED" and c["HEALTH"] == "PASS" and c["RELEASE_IDENTITY"] == "FAIL"


def test_worker_self_report_cannot_create_success_via_seam():
    # The request carries a worker self-report screaming success; the observed target is empty.
    # The verifier must REJECT — it never consults the worker's claim.
    r = _verify(_observation(files=(), present=False, health=None, contact="NOT_OBSERVED"))
    assert r.verdict == "REJECTED"
