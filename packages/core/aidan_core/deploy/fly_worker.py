"""Fly Machines deploy WorkerAdapter (Gate 6 real-deploy readiness) — provider ``fly-machines``.

An ordinary Gate-4 ``WorkerAdapter`` (no DB, no canonical authority, claim only) that deploys the
EXACT frozen release artifact to a venture-owned Fly app through an injectable transport, then
returns the durable machine identity as an inert CLAIM. It NEVER self-certifies success: canonical
deployment SUCCESS comes solely from the independent ``FlyDeploymentObserver`` + deterministic
verifier. The worker only creates the machine and reports what it did.

Honest boundary observability (all inert claims; the verifier re-reads reality):
  - local transport attempt made
  - Fly API contact   OBSERVED / NOT_OBSERVED / UNKNOWN
  - machine-create effect OBSERVED / NOT_OBSERVED / UNKNOWN

Failure taxonomy:
  - pre-transport guard / definitive provider rejection / timeout-before-send -> DeployAdapterError
    (WORKER_ERROR, capital released, NO effect, NO cost)                              [FAILED]
  - machine created but the frozen outcome is a known failure                        -> not used
    here (running-state/health are the VERIFIER's call, so a created machine is returned as a claim)
  - possible-but-unresolved create effect after the request was sent -> AmbiguousExternalEffectError
    (RECOVERY_REQUIRED; reservation held; never blind-retried)                        [RECOVERY_REQUIRED]

The credential is read from the host env per invocation, passed only in the Authorization header,
never persisted or logged. The worker deploys ONLY the frozen digest-pinned image; it cannot
substitute another digest.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from ..errors import AmbiguousExternalEffectError, DeployAdapterError
from ..factory.workers import WorkerRequest, WorkerResult
from . import artifact as artifact_mod
from .fly_transport import PHASE_PRE_SEND, FlyTransportError, HttpFlyTransport

WORKER_KIND = "fly-machines"
WORKER_VERSION = "1"
TOKEN_ENV = "DEPLOY_FLY_API_TOKEN"


def _machine_name(action_request_id: str) -> str:
    """Deterministic, per-action machine name so an interrupted create can be reconciled by name
    (Fly machine names are unique per app) rather than blind-creating a duplicate."""
    slug = re.sub(r"[^a-z0-9]", "", str(action_request_id).lower())[:20]
    return f"aidan-{slug}"


def _machine_config(image: str, runtime_contract, health_contract) -> dict:
    """Build the EXACT Fly Machine config from FROZEN authority (the worker invents nothing). The
    runtime_contract must expose an HTTP service so <app>.fly.dev routes to the machine; missing it
    means the observer's external health route cannot be satisfied -> refuse to create."""
    rc = dict(runtime_contract or {})
    internal_port = rc.get("internal_port")
    ports = rc.get("ports")
    if not isinstance(internal_port, int) or not ports:
        raise DeployAdapterError("FLY_RUNTIME_CONTRACT_MISSING")
    config = {"image": image, "services": [{
        "protocol": rc.get("protocol", "tcp"),
        "internal_port": internal_port,
        "ports": ports,
    }]}
    # Optional bounded Fly-side HTTP health check, derived from the frozen health path.
    hc = dict(health_contract or {})
    path = hc.get("path")
    if path:
        config["checks"] = {"http": {"type": "http", "port": internal_port, "method": "GET",
                                     "path": path, "interval": "15s", "timeout": "10s"}}
    return config


def cleanup_machine(transport, token, app, machine_id, *, timeout=30.0, confirm_reads=4,
                    backoff=1.0, sleep=None) -> str:
    """Governed teardown of EXACTLY the created machine: ONE force DELETE, then BOUNDED read-only
    confirmation retries (the DELETE is NEVER re-issued). Returns CLEANUP_CONFIRMED (a confirmation
    GET saw 404), CLEANUP_FAILED (the machine was definitely still present and never 404), or
    CLEANUP_AMBIGUOUS (all confirmation reads inconclusive). ``confirm_reads`` bounds the number of
    read-only confirmations; ``sleep`` is injectable so tests avoid real backoff."""
    if not machine_id or not app:
        return "CLEANUP_AMBIGUOUS"
    _sleep = sleep or time.sleep
    try:
        transport("DELETE", f"/apps/{app}/machines/{machine_id}?force=true", token=token, timeout=timeout)
    except FlyTransportError:
        pass   # the DELETE may or may not have taken effect; read-only confirmation decides. No retry.

    present_seen = False
    reads = max(1, int(confirm_reads))
    for i in range(reads):
        try:
            resp = transport("GET", f"/apps/{app}/machines/{machine_id}", token=token, timeout=timeout)
        except FlyTransportError:
            resp = None                 # transient/unreachable read -> inconclusive, keep trying
        if resp is not None:
            if resp.status == 404:
                return "CLEANUP_CONFIRMED"      # independently confirmed absent
            if resp.status == 200 and (resp.body or {}).get("state") not in (None, "destroyed"):
                present_seen = True             # definitely still present (keep confirming until bound)
        if i < reads - 1:
            _sleep(backoff)
    return "CLEANUP_FAILED" if present_seen else "CLEANUP_AMBIGUOUS"


class FlyMachinesWorker:
    """Deploy the frozen release image to a venture-owned Fly app via the Machines API."""

    kind = WORKER_KIND

    def __init__(self, *, transport=None, token_env: str = TOKEN_ENV, wait_timeout: float = 30.0):
        self._transport = transport or HttpFlyTransport()
        self._token_env = token_env
        self._wait_timeout = wait_timeout

    # ---- helpers ---------------------------------------------------------------------
    def _deploy_block(self, request: WorkerRequest) -> dict:
        block = dict((request.task_payload or {}).get("deploy", {}))
        if not block:
            raise DeployAdapterError("FLY_NO_DEPLOY_AUTHORITY")
        return block

    def execute(self, request: WorkerRequest) -> WorkerResult:  # no DB connection
        block = self._deploy_block(request)
        rc = dict(block.get("release_contract", {}))

        # ---- pre-transport guards: definitive, no external effect (WORKER_ERROR, released) --------
        if block.get("provider_kind") != WORKER_KIND:
            raise DeployAdapterError("FLY_WRONG_PROVIDER")
        token = os.environ.get(self._token_env)
        if not token:
            raise DeployAdapterError("FLY_AUTH_MISSING")
        app = block.get("target_ref")
        if not app:
            raise DeployAdapterError("FLY_TARGET_MISSING")
        expected = rc.get("expected_artifact_identity")
        frozen_digest = artifact_mod.expected_digest(expected)
        if not frozen_digest:
            raise DeployAdapterError("FLY_ARTIFACT_DIGEST_MISSING")
        image = rc.get("image_ref")
        if not image:
            raise DeployAdapterError("FLY_IMAGE_REF_MISSING")
        # The worker deploys ONLY the frozen digest: the image ref MUST be digest-pinned and its
        # digest MUST equal the frozen expected digest. It can never substitute another digest.
        try:
            if artifact_mod.normalize_digest(image) != frozen_digest:
                raise DeployAdapterError("FLY_IMAGE_DIGEST_MISMATCH")
        except artifact_mod.ArtifactIdentityError as exc:
            raise DeployAdapterError("FLY_IMAGE_NOT_DIGEST_PINNED") from exc
        # The Machine service/port config that makes <app>.fly.dev externally reachable is FROZEN
        # authority, not invented by the worker. Without it the observer's health route cannot be
        # satisfied, so refuse to create anything.
        config = _machine_config(image, rc.get("runtime_contract"), rc.get("health_contract"))

        name = _machine_name(request.action_request_id)
        region = rc.get("region")
        timeout = float(request.timeout_seconds or 60)

        # ---- create the machine (the consequential mutation) --------------------------------------
        body = {"name": name, "config": config}
        if region:
            body["region"] = region
        try:
            resp = self._transport("POST", f"/apps/{app}/machines", token=token, body=body,
                                   timeout=timeout)
        except FlyTransportError as exc:
            if exc.phase == PHASE_PRE_SEND:
                # Provably NOT transmitted -> no machine, no cost. Clean no-effect failure.
                raise DeployAdapterError("FLY_CREATE_NOT_SENT") from exc
            # The request MAY have reached Fly -> reconcile by name; never blind-create a second one.
            machine = self._reconcile(app, name, token, timeout)
            return self._claim(machine, app)

        status = resp.status
        if status in (200, 201):
            machine = resp.body or {}
            if not machine.get("id"):
                # Provider acknowledged but returned no machine id -> reconcile by name.
                machine = self._reconcile(app, name, token, timeout)
            return self._claim(machine, app)
        if 400 <= status < 500:
            # Definitive provider rejection: the create was refused; no machine exists. Known FAILED.
            raise DeployAdapterError(f"FLY_CREATE_REJECTED_{status}")
        # 5xx / unexpected: the create may or may not have taken effect -> reconcile.
        machine = self._reconcile(app, name, token, timeout)
        return self._claim(machine, app)

    def _reconcile(self, app: str, name: str, token: str, timeout: float) -> dict:
        """Read-only reconciliation after an ambiguous create. Find a machine with the deterministic
        name: present -> use it (no duplicate); provably absent -> FAILED (no effect); otherwise the
        effect is unresolved -> RECOVERY_REQUIRED."""
        try:
            resp = self._transport("GET", f"/apps/{app}/machines", token=token, timeout=timeout)
        except FlyTransportError as exc:
            raise AmbiguousExternalEffectError("FLY_RECONCILE_UNREACHABLE") from exc
        if resp.status != 200 or not isinstance(resp.body, dict) and not isinstance(resp.body, list):
            raise AmbiguousExternalEffectError("FLY_RECONCILE_INCONCLUSIVE")
        machines = resp.body if isinstance(resp.body, list) else (resp.body.get("machines") or [])
        for m in machines:
            if isinstance(m, dict) and m.get("name") == name:
                return m
        # The list was read successfully and our named machine is absent -> the create had no effect.
        raise DeployAdapterError("FLY_CREATE_NO_EFFECT")

    def _claim(self, machine: dict, app: str) -> WorkerResult:
        """Return the durable machine identity as an INERT claim. The observer/verifier re-read the
        real state + digest + health; nothing here is trusted as success."""
        machine = machine or {}
        mid = machine.get("id")
        if not mid:
            # Can't even name the machine -> treat as an unresolved effect (do not fabricate an id).
            raise AmbiguousExternalEffectError("FLY_CREATE_UNIDENTIFIED")
        structured = {
            "provider": WORKER_KIND,
            "app": app,
            "machine_id": mid,
            "instance_id": machine.get("instance_id"),
            # bounded, inert claims (verifier re-reads reality):
            "provider_contact": "OBSERVED",
            "create_effect": "OBSERVED",
            "claimed_state": machine.get("state"),
            "claimed_digest": ((machine.get("image_ref") or {}).get("digest")),
        }
        return WorkerResult(
            worker_kind=self.kind, worker_version=WORKER_VERSION,
            external_result_id=f"fly-machine:{mid}",
            reported_outcome="fly-machines-created",   # a CLAIM; the verifier decides correctness
            structured_output=structured, artifacts=(),
        )
