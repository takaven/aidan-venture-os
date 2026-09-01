"""Governed deployment dispatch composition (Gate 6 Slice 1).

No second execution/worker runtime: a deploy worker is a replaceable Gate 4
``WorkerAdapter``. Deploy dispatch composes the frozen release authority onto the
EXISTING Gate 4 path — it binds the immutable ``release_candidate`` (id + release_hash
+ target) into the immutable ``execution_spec`` (whose creation independently enforces
the deploy-authority guard for ANY caller), then dispatches through
``factory.runtime.execute_action``.

Slice 1 stops at bounded worker dispatch/claim capture: NO external deployment, NO
release verification, NO Proof Receipt, NO lifecycle transition. The deploy worker's
result — including any ``deployed``/``release_verified``/``lifecycle`` claim — is inert
``WorkerResult`` data. Authorization is obtained by the Gate 4 runtime only AFTER the
release intent is frozen into the immutable execution spec.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .. import policy
from ..errors import DeployAuthorityError, NotFoundError
from ..build import substrate as substrate_mod
from ..factory import runtime as factory_runtime
from ..factory import spec as spec_mod
from ..factory.workers import WorkerRegistry
from . import release as release_mod
from . import target as target_mod
from .verifiers import deploy_verifier_registry

# Deploy capability vocabulary this slice authorizes (mirrors the DB CHECK extension).
DEPLOY_CAPABILITIES = ("DEPLOY_CANDIDATE",)
# The deploy verifier is forced: a deploy spec can only be completed by the
# deterministic deployment verifier, never by a worker-self-report verifier.
DEPLOY_VERIFIER_KIND = "deployment-release"
# Provider kinds that deploy to a REAL external target (not the local controlled dir) and therefore
# MUST freeze an immutable artifact identity (digest) as the release authority. Provider-neutral set.
EXTERNAL_PROVIDER_KINDS = ("fly-machines",)


def deploy_target_path(venture_id, deployment_target_id) -> str:
    """Deterministic, venture-isolated local target path (proves architecture, not cloud)."""
    return str(Path(tempfile.gettempdir()) / "aidan-deploy" / str(venture_id) / str(deployment_target_id))


@dataclass(frozen=True)
class DeployInput:
    """Typed deploy-specific data carried inside the canonical WorkerRequest.

    Reconstructed by a deploy ``WorkerAdapter`` from ``request.task_payload['deploy']``.
    Gives the worker the exact release identity + target ref to deploy — no DB access,
    no authority to choose a different release.
    """

    venture_id: str
    release_candidate_id: str
    release_hash: str
    deployment_target_id: str
    target_ref: str
    provider_kind: str
    release_contract: dict[str, Any]

    @classmethod
    def from_worker_request(cls, request) -> "DeployInput":
        block = dict((request.task_payload or {}).get("deploy", {}))
        if not block:
            raise DeployAuthorityError("worker request carries no bound release authority")
        return cls(
            venture_id=str(request.venture_id),
            release_candidate_id=str(block["release_candidate_id"]),
            release_hash=str(block["release_hash"]),
            deployment_target_id=str(block["deployment_target_id"]),
            target_ref=str(block["target_ref"]),
            provider_kind=str(block.get("provider_kind", "")),
            release_contract=dict(block.get("release_contract", {})),
        )


@dataclass(frozen=True)
class DeployDispatch:
    action_request_id: str
    release_candidate_id: str
    release_hash: str
    deployment_target_id: str
    execution_spec_id: str
    execution_spec_created: bool


def _build_deploy_bundle(conn, rc_row) -> tuple:
    """Reconstruct the exact frozen release bundle deterministically from durable state.

    The bundle = the build_manifest's substrate + candidate files (with content). The
    kernel — not the worker — determines WHAT bytes the release is. A compliant deploy
    worker writes exactly this bundle; the verifier independently re-hashes the target.
    """
    build_manifest_id, expected_tree_hash = rc_row[3], rc_row[6]
    with conn.cursor() as cur:
        cur.execute("SELECT substrate_release_id, execution_attempt_id FROM build_manifest WHERE id = %s",
                    (build_manifest_id,))
        m = cur.fetchone()
        if m is None:
            raise NotFoundError(f"build_manifest {build_manifest_id} does not exist")
        substrate_release_id, attempt_id = m
        cur.execute("SELECT raw_payload FROM execution_result WHERE execution_attempt_id = %s "
                    "ORDER BY received_at DESC, id DESC LIMIT 1", (attempt_id,))
        rrow = cur.fetchone()
    candidate = list(((rrow[0] if rrow else {}) or {}).get("structured_output", {}).get("candidate_files", []))

    release_row = substrate_mod.get_substrate_release(conn, substrate_release_id)
    tmp = tempfile.mkdtemp(prefix="deploy-bundle-")
    bundle: list[dict] = []
    try:
        # Carry the EXACT deployed bytes as lossless latin-1 (platform-independent), so a
        # compliant deploy reproduces the manifest's kernel-computed hashes exactly.
        for entry in substrate_mod.materialize_substrate(release_row, tmp):
            raw = (Path(tmp) / entry["path"]).read_bytes()
            bundle.append({"path": entry["path"], "content": raw.decode("latin-1")})
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    for c in candidate:
        raw = str(c["content"]).encode("utf-8")   # manifest hashes candidate bytes as utf-8
        bundle.append({"path": c["path"], "content": raw.decode("latin-1")})
    return bundle, expected_tree_hash


def _deploy_contract(rc_row, target_row, target_path, expected_tree_hash) -> dict:
    (_id, venture_id, _arid, _mid, _bsid, _tid, _tree, release_contract, release_hash, _created) = rc_row
    rc = dict(release_contract or {})
    contract = {
        "target_path": target_path,
        "candidate_tree_hash": expected_tree_hash,
        "venture_id": str(venture_id),
        "deployment_target_id": str(_tid),
        "release_candidate_id": str(_id),
        "release_hash": release_hash,
        "release_contract": rc,
    }
    # Real external deploy: surface the frozen artifact identity + provider identity so the verifier
    # runs digest-mode checks (compare provider read-back digest to the frozen expected digest). The
    # LOCAL path carries no expected_artifact_identity and is byte/evidence-identical to Slices 1-3.
    if rc.get("expected_artifact_identity"):
        contract["expected_artifact_identity"] = rc["expected_artifact_identity"]
        contract["required_state"] = rc.get("required_state", "started")
        contract["provider_kind"] = target_row[3]
        contract["target_ref"] = target_row[4]
    return contract


def prepare_deploy_execution(
    conn,
    action_request_id: str,
    *,
    worker_kind: str,
    timeout_seconds: int = 60,
    max_attempts: int = 1,
    capability_scope=DEPLOY_CAPABILITIES,
    actor: str = "factory",
) -> DeployDispatch:
    """Bind a frozen release_candidate + target into an immutable Gate-4 execution spec.

    Forces the deterministic deployment verifier (``deployment-release``), embeds the
    kernel-reconstructed release bundle + venture-isolated target path for the worker,
    and freezes the deployment verification contract for the verifier. Idempotent; the
    generic ``create_execution_spec`` deploy guard independently rejects any mismatch.
    """
    rc_row = release_mod.get_release_candidate(conn, action_request_id)
    if rc_row is None:
        raise DeployAuthorityError(
            f"no frozen release_candidate for action {action_request_id}; "
            "a deploy execution cannot be prepared from free-form intent"
        )
    target_row = target_mod.get_deployment_target(conn, rc_row[5])
    if target_row is None:
        raise DeployAuthorityError(f"deployment_target {rc_row[5]} does not exist")

    # A real external provider deploy CANNOT be dispatched without a frozen immutable artifact
    # identity bound into the release: the digest is the release authority the verifier reads back.
    rc_contract = dict(rc_row[7] or {})
    if target_row[3] in EXTERNAL_PROVIDER_KINDS and not rc_contract.get("expected_artifact_identity"):
        raise DeployAuthorityError(
            f"provider {target_row[3]!r} requires a frozen expected_artifact_identity in the "
            "release_contract; a real external deploy cannot be prepared without it")

    bundle, expected_tree_hash = _build_deploy_bundle(conn, rc_row)
    target_path = deploy_target_path(rc_row[1], rc_row[5])
    task_payload = {
        "deploy": {
            "release_candidate_id": str(rc_row[0]),
            "release_hash": rc_row[8],
            "deployment_target_id": str(rc_row[5]),
            "environment": target_row[2],
            "provider_kind": target_row[3],
            "target_ref": target_row[4],
            "target_path": target_path,
            "release_contract": dict(rc_row[7] or {}),
            "deploy_bundle": bundle,
        }
    }
    expected_output_contract = {"deployment": _deploy_contract(rc_row, target_row, target_path, expected_tree_hash)}
    spec = spec_mod.create_execution_spec(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=DEPLOY_VERIFIER_KIND,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=list(capability_scope), task_payload=task_payload,
        expected_output_contract=expected_output_contract, actor=actor,
    )
    return DeployDispatch(
        action_request_id=str(action_request_id), release_candidate_id=str(rc_row[0]),
        release_hash=rc_row[8], deployment_target_id=str(rc_row[5]),
        execution_spec_id=str(spec.spec_id), execution_spec_created=spec.created,
    )


def execute_deploy(
    conn,
    action_request_id: str,
    *,
    registry: WorkerRegistry,
    worker_kind: str,
    timeout_seconds: int = 60,
    max_attempts: int = 1,
    capability_scope=DEPLOY_CAPABILITIES,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    safety_mode: str = "IDEMPOTENT",
    lease_seconds: float = 300,
    clock=None,
    actor: str = "factory",
):
    """Bind release authority and dispatch the deploy worker through the Gate 4 runtime.

    Dispatch captures the worker result as a CLAIM only; canonical deployment SUCCESS
    comes solely from ``verify_deploy`` (the deterministic verifier + proof). Fresh
    post-spec authorization is obtained by the Gate 4 runtime.
    """
    dispatch = prepare_deploy_execution(
        conn, action_request_id, worker_kind=worker_kind, timeout_seconds=timeout_seconds,
        max_attempts=max_attempts, capability_scope=capability_scope, actor=actor,
    )
    result = factory_runtime.execute_action(
        conn, action_request_id, registry=registry,
        workspace_ref=f"deploy://{dispatch.deployment_target_id}",
        approval_threshold=approval_threshold, safety_mode=safety_mode,
        lease_seconds=lease_seconds, clock=clock, actor=actor,
    )
    return dispatch, result


def verify_deploy(conn, action_request_id: str, *, actual_cost, actor: str = "factory",
                  observer_factory=None):
    """Deterministically verify the deployed release and, only if VERIFIED, complete via
    the canonical proof-gated path — the ONE proof_receipt, no second proof system.

    Reuses ``factory.runtime.verify_and_complete`` with the deployment verifier registry;
    the deployment verifier independently inspects the actual target (a worker's
    ``deployed=true`` claim is inert). ``observer_factory`` selects the read-back — the controlled
    LOCAL target by default, or a real provider read-back (e.g. Fly) for an external deploy. No
    lifecycle transition occurs here.
    """
    return factory_runtime.verify_and_complete(
        conn, action_request_id, verifier_registry=deploy_verifier_registry(observer_factory),
        actual_cost=actual_cost, actor=actor,
    )
