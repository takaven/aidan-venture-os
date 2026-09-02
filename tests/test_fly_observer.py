"""Deterministic Fly independent-observer + verifier matrix — NO Fly calls (fake transport only).

Covers matrix rows J,K,L,M,N: the ``FlyDeploymentObserver`` reads a machine back independently and
the forced ``deployment-release`` verifier decides SUCCESS from that read-back — never from the
worker's self-report. Digest mode is selected by ``expected_artifact_identity`` in the contract.
"""
from __future__ import annotations

from aidan_core.deploy.fly_transport import FlyResponse, FlyTransportError, PHASE_POST_SEND
from aidan_core.deploy.observe import FlyDeploymentObserver
from aidan_core.deploy.verifiers import DeploymentReleaseVerifier
from aidan_core.factory.verifiers import VerificationRequest

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
APP = "aidan-smoke-app"
VEN, TID, MID = "ven-1", "tgt-1", "m-1"


class FakeFly:
    def __init__(self, get=None, raise_get=False):
        self._get = get
        self._raise = raise_get

    def __call__(self, method, path, *, token, body=None, timeout=None):
        if self._raise:
            raise FlyTransportError("t", phase=PHASE_POST_SEND)
        return self._get if self._get is not None else FlyResponse(404, {})


def _machine(**over):
    m = {"id": MID, "instance_id": "i-1", "state": "started", "image_ref": {"digest": DIGEST}}
    m.update(over)
    return m


def _observer(fake, *, health="ok"):
    return FlyDeploymentObserver(
        fly_transport=fake, token="t", app=APP, machine_id=MID, venture_id=VEN,
        deployment_target_id=TID, health_probe=(lambda: health),
        poll_attempts=3, poll_backoff=0.0, sleep=lambda *_: None)


def _request(*, worker_claim=None):
    contract = {
        "venture_id": VEN, "deployment_target_id": TID,
        "expected_artifact_identity": {"kind": "oci-image-digest", "digest": DIGEST},
        "required_state": "started", "release_candidate_id": "rc-1", "release_hash": "rh-1",
        "release_contract": {"health_contract": {"marker_content": "ok"}},
    }
    return VerificationRequest(
        action_request_id="a-1", execution_attempt_id="att-1", verifier_kind="deployment-release",
        expected_output_contract={"deployment": contract},
        worker_structured_output=worker_claim or {}, artifacts=(), spec_hash="sh")


def _verify(fake, *, health="ok", worker_claim=None):
    v = DeploymentReleaseVerifier(observer_factory=lambda c: _observer(fake, health=health))
    return v.verify(_request(worker_claim=worker_claim))


def _checks(res):
    return {c["name"]: c["result"] for c in res.detail["checks"]}


# ---- N: correct independent observation -> VERIFIED ----------------------------------
def test_N_correct_readback_verifies():
    res = _verify(FakeFly(get=FlyResponse(200, _machine())))
    assert res.verdict == "VERIFIED" and all(v == "PASS" for v in _checks(res).values())
    assert res.detail["read_back_contact"] == "OBSERVED"


# ---- J: wrong observed digest -> REJECTED --------------------------------------------
def test_J_wrong_observed_digest_rejected():
    res = _verify(FakeFly(get=FlyResponse(200, _machine(image_ref={"digest": OTHER}))))
    assert res.verdict == "REJECTED" and _checks(res)["ARTIFACT_IDENTITY"] == "FAIL"


# ---- K: right digest + wrong target (machine not under frozen app) -> REJECTED --------
def test_K_wrong_target_rejected():
    res = _verify(FakeFly(get=FlyResponse(404, {})))     # 404 under the frozen app
    c = _checks(res)
    assert res.verdict == "REJECTED" and c["TARGET_EXISTS"] == "FAIL"


# ---- L: right digest + unhealthy runtime -> REJECTED ---------------------------------
def test_L_unhealthy_rejected():
    res = _verify(FakeFly(get=FlyResponse(200, _machine())), health=None)
    c = _checks(res)
    assert res.verdict == "REJECTED" and c["HEALTH"] == "FAIL" and c["ARTIFACT_IDENTITY"] == "PASS"


def test_runtime_state_not_started_rejected():
    res = _verify(FakeFly(get=FlyResponse(200, _machine(state="created"))), health=None)
    assert res.verdict == "REJECTED" and _checks(res)["RUNTIME_STATE"] == "FAIL"


# ---- M: worker says deployed=true but observer fails -> no VERIFIED -------------------
def test_M_worker_self_report_cannot_create_success():
    loud = {"deployed": True, "release_verified": True, "health": "green", "overall_success": True}
    res = _verify(FakeFly(get=FlyResponse(404, {})), worker_claim=loud)
    assert res.verdict == "REJECTED"      # the loud claim is ignored; read-back decides


def test_unreachable_readback_is_rejected_contact_unknown():
    res = _verify(FakeFly(raise_get=True))
    assert res.verdict == "REJECTED" and res.detail["read_back_contact"] == "UNKNOWN"


# ---- bounded READ-ONLY convergence polling (state/health race) ------------------------
from aidan_core.deploy.checks import evaluate_artifact_observation


class SeqFly:
    """Returns GET responses in sequence (last repeats). Records calls; never mutates."""

    def __init__(self, gets):
        self.gets, self.i, self.calls = list(gets), 0, []

    def __call__(self, method, path, *, token, body=None, timeout=None):
        self.calls.append((method, path))
        item = self.gets[min(self.i, len(self.gets) - 1)]
        self.i += 1
        if isinstance(item, Exception):
            raise item
        return item

    def count(self, m):
        return sum(1 for c in self.calls if c[0] == m)


def _seq_health(seq):
    box = {"i": 0}
    def _p():
        v = seq[min(box["i"], len(seq) - 1)]
        box["i"] += 1
        return v
    return _p


def _obs_seq(fake, health_probe):
    return FlyDeploymentObserver(
        fly_transport=fake, token="t", app=APP, machine_id=MID, venture_id=VEN,
        deployment_target_id=TID, health_probe=health_probe,
        poll_attempts=6, poll_backoff=0.0, sleep=lambda *_: None).observe()


def _checks_of(obs):
    res = evaluate_artifact_observation(
        obs, venture_id=VEN, deployment_target_id=TID,
        expected_artifact_identity={"kind": "oci-image-digest", "digest": DIGEST},
        release_contract={"health_contract": {"marker_content": "ok"}}, required_state="started")
    return {r.name: r.result for r in res}


def test_H_starting_then_started_healthy_converges_pass():
    fake = SeqFly([FlyResponse(200, _machine(state="created")), FlyResponse(200, _machine(state="started"))])
    obs = _obs_seq(fake, _seq_health(["ok"]))
    assert obs.running_state == "started" and obs.health_marker == "ok"
    assert all(v == "PASS" for v in _checks_of(obs).values())
    assert fake.count("DELETE") == 0 and fake.count("POST") == 0     # observer never mutates


def test_I_started_health_initially_unavailable_then_healthy_pass():
    fake = SeqFly([FlyResponse(200, _machine(state="started"))])     # always started
    obs = _obs_seq(fake, _seq_health([None, "ok"]))                  # health converges on 2nd probe
    assert obs.health_marker == "ok" and all(v == "PASS" for v in _checks_of(obs).values())


def test_J_starting_forever_bounded_fail_no_mutation():
    fake = SeqFly([FlyResponse(200, _machine(state="created"))])     # never reaches started
    obs = _obs_seq(fake, _seq_health([None]))
    assert obs.running_state == "created" and _checks_of(obs)["RUNTIME_STATE"] == "FAIL"
    assert fake.count("GET") == 6 and fake.count("DELETE") == 0      # bounded reads, no mutation


def test_L_health_marker_never_appears_bounded_fail():
    fake = SeqFly([FlyResponse(200, _machine(state="started"))])
    obs = _obs_seq(fake, _seq_health([None]))                        # health never matches
    assert _checks_of(obs)["HEALTH"] == "FAIL" and obs.running_state == "started"


def test_M_destroyed_during_convergence_fails_closed():
    fake = SeqFly([FlyResponse(200, _machine(state="created")), FlyResponse(200, _machine(state="destroyed"))])
    obs = _obs_seq(fake, _seq_health(["ok"]))
    assert obs.target_present is False and _checks_of(obs)["TARGET_EXISTS"] == "FAIL"


def test_D_destroyed_record_target_absent():
    fake = SeqFly([FlyResponse(200, _machine(state="destroyed"))])
    obs = _obs_seq(fake, _seq_health(["ok"]))
    assert obs.target_present is False and _checks_of(obs)["TARGET_EXISTS"] == "FAIL"


def test_N_attempt_history_has_state_digest_health():
    fake = SeqFly([FlyResponse(200, _machine(state="created")), FlyResponse(200, _machine(state="started"))])
    obs = _obs_seq(fake, _seq_health(["ok"]))
    assert len(obs.attempts) >= 2
    last = obs.attempts[-1]
    assert set(last) >= {"attempt", "get", "runtime_state", "artifact_digest", "health_attempted", "health_matched"}
    assert last["runtime_state"] == "started" and last["artifact_digest"] == DIGEST and last["health_matched"] is True
