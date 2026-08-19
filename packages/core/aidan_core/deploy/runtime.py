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

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from .. import policy
from ..errors import DeployAuthorityError
from ..factory import runtime as factory_runtime
from ..factory import spec as spec_mod
from ..factory.workers import WorkerRegistry
from . import release as release_mod
from . import target as target_mod

# Deploy capability vocabulary this slice authorizes (mirrors the DB CHECK extension).
DEPLOY_CAPABILITIES = ("DEPLOY_CANDIDATE",)


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


def _deploy_task_payload(rc_row, target_row) -> dict:
    (_id, _v, _arid, _mid, _bsid, _tid, _tree, release_contract, release_hash, _created) = rc_row
    (_tid2, _tv, environment, provider_kind, target_ref, _prov, _tc) = target_row
    return {
        "deploy": {
            "release_candidate_id": str(_id),
            "release_hash": release_hash,
            "deployment_target_id": str(_tid),
            "environment": environment,
            "provider_kind": provider_kind,
            "target_ref": target_ref,
            "release_contract": dict(release_contract or {}),
        }
    }


def prepare_deploy_execution(
    conn,
    action_request_id: str,
    *,
    worker_kind: str,
    verifier_kind: str,
    timeout_seconds: int,
    max_attempts: int,
    capability_scope=DEPLOY_CAPABILITIES,
    actor: str = "factory",
) -> DeployDispatch:
    """Bind a frozen release_candidate + target into an immutable Gate-4 execution spec.
    Idempotent; does not dispatch.

    Requires the release_candidate (quality-qualified) and its target to exist. The
    execution_spec's task_payload embeds the release id + hash + target, and the generic
    ``create_execution_spec`` deploy guard independently rejects any mismatch — so a
    changed release can never mutate an existing execution spec (hard conflict).
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

    task_payload = _deploy_task_payload(rc_row, target_row)
    spec = spec_mod.create_execution_spec(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=list(capability_scope), task_payload=task_payload,
        expected_output_contract=dict(rc_row[7] or {}), actor=actor,
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
    verifier_kind: str,
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

    Slice 1 stops at result capture: NO verification, NO deployment success, NO proof,
    NO lifecycle. The worker result is a CLAIM only. Authorization is obtained by the
    Gate 4 runtime only after the release intent is frozen into the immutable exec spec.
    """
    dispatch = prepare_deploy_execution(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=capability_scope, actor=actor,
    )
    result = factory_runtime.execute_action(
        conn, action_request_id, registry=registry, workspace_ref=f"deploy://{dispatch.deployment_target_id}",
        approval_threshold=approval_threshold, safety_mode=safety_mode,
        lease_seconds=lease_seconds, clock=clock, actor=actor,
    )
    return dispatch, result
