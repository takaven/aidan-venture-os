"""Governed build dispatch composition (Gate 5 Slice 1).

This module contains NO second execution/worker runtime. A Builder is a
replaceable Gate 4 ``WorkerAdapter``; build dispatch composes the frozen build
authority onto the EXISTING Gate 4 path:

1. validate build authority (a frozen ``build_spec`` + a registered isolated
   ``venture_repository`` that is not the canonical OS repo);
2. freeze/read the immutable Gate 4 ``execution_spec`` whose ``task_payload`` binds
   the build_spec identity+hash, the venture repository, and the venture-specific
   product intent the worker must implement;
3. dispatch through the existing ``factory.runtime.execute_action`` (which
   re-authorizes against the frozen spec, claims a canonical attempt, and captures
   the worker's result as a CLAIM only).

The builder-specific typed contract (:class:`BuildInput`) is carried INSIDE the
canonical ``WorkerRequest.task_payload`` — there is no ``BuilderAdapter``, no
``BuilderRegistry``, and no separate dispatch/verify/retry loop. Authorization
used for dispatch is obtained by the Gate 4 runtime only AFTER the build intent
is frozen into the immutable execution spec; the historical Gate 3 Policy
decision that predates the build spec cannot authorize builder dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from .. import policy
from ..errors import BuildAuthorityError, NotFoundError
from ..factory import runtime as factory_runtime
from ..factory import spec as spec_mod
from ..factory.workers import WorkerRegistry
from . import manifest as manifest_mod
from . import quality as quality_mod
from . import repository as repo_mod
from . import spec as build_spec_mod
from . import technical as technical_mod
from . import workspace as ws


@dataclass(frozen=True)
class BuildInput:
    """Typed builder-specific data carried inside the canonical WorkerRequest.

    A specialist builder ``WorkerAdapter`` reconstructs this from
    ``request.task_payload['build']`` (and ``request.workspace_ref``). It gives the
    worker the venture-specific product intent it must implement and the exact
    frozen build/repository identity — but no DB access and no authority.
    """

    venture_id: str
    build_spec_id: str
    build_spec_hash: str
    venture_repository_id: str
    repository_ref: str
    intent: dict[str, Any]

    @classmethod
    def from_worker_request(cls, request) -> "BuildInput":
        block = dict((request.task_payload or {}).get("build", {}))
        if not block:
            raise BuildAuthorityError("worker request carries no bound build authority")
        return cls(
            venture_id=str(request.venture_id),
            build_spec_id=str(block["build_spec_id"]),
            build_spec_hash=str(block["build_spec_hash"]),
            venture_repository_id=str(block["venture_repository_id"]),
            repository_ref=str(block["repository_ref"]),
            intent=dict(block.get("intent", {})),
        )


@dataclass(frozen=True)
class BuildDispatch:
    action_request_id: str
    build_spec_id: str
    build_spec_hash: str
    venture_repository_id: str
    repository_ref: str
    execution_spec_id: str
    execution_spec_created: bool


def _build_task_payload(build_row, repo_id, repository_ref, declared_inputs) -> dict:
    """Deterministic execution_spec task_payload binding the frozen build authority."""
    (_id, _venture, _arid, _dec, _rec, _opp, buyer, problem, value_proposition,
     product_category, primary_workflow, differentiators, required_capabilities,
     excluded_capabilities, experience_principles, _contract, spec_hash, _created) = build_row
    return {
        "build": {
            "build_spec_id": str(_id),
            "build_spec_hash": spec_hash,
            "venture_repository_id": str(repo_id),
            "repository_ref": repository_ref,
            "intent": {
                "buyer": buyer,
                "problem": problem,
                "value_proposition": value_proposition,
                "product_category": product_category,
                "primary_workflow": primary_workflow,
                "differentiators": list(differentiators or []),
                "required_capabilities": list(required_capabilities or []),
                "excluded_capabilities": list(excluded_capabilities or []),
                "experience_principles": list(experience_principles or []),
            },
        },
        "declared_inputs": dict(declared_inputs or {}),
    }


def prepare_build_execution(
    conn,
    action_request_id: str,
    *,
    worker_kind: str,
    verifier_kind: str,
    capability_scope: list,
    timeout_seconds: int,
    max_attempts: int,
    declared_inputs: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> BuildDispatch:
    """Bind a frozen build_spec + isolated venture repository into an immutable
    Gate 4 execution spec. Idempotent; does not dispatch.

    Requires the build_spec to be frozen first (BUILD is not direct coding
    authority) and the venture repository to be registered and non-OS. The
    resulting execution_spec's ``task_payload`` deterministically embeds the
    build_spec hash, so a materially changed build intent (a different build_spec)
    can never mutate an existing execution spec — it is a hard idempotency
    conflict, exactly as Gate 4 requires.
    """
    build_row = build_spec_mod.get_build_spec(conn, action_request_id)
    if build_row is None:
        raise BuildAuthorityError(
            f"no frozen build_spec for action {action_request_id}; "
            "a BUILD decision is not direct coding authority"
        )
    venture_id = build_row[1]
    build_spec_id = build_row[0]
    build_spec_hash = build_row[16]
    expected_output_contract = dict(build_row[15] or {})

    repo_row = repo_mod.get_venture_repository(conn, venture_id)
    if repo_row is None:
        raise BuildAuthorityError(
            f"venture {venture_id} has no registered venture_repository; cannot build"
        )
    repo_id, repository_ref = repo_row[0], repo_row[2]
    # Defense in depth: never dispatch a builder against the canonical OS repo.
    repo_mod.assert_isolated_repository_ref(repository_ref)

    task_payload = _build_task_payload(build_row, repo_id, repository_ref, declared_inputs)
    spec = spec_mod.create_execution_spec(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=list(capability_scope), task_payload=task_payload,
        expected_output_contract=expected_output_contract, actor=actor,
    )
    return BuildDispatch(
        action_request_id=str(action_request_id), build_spec_id=str(build_spec_id),
        build_spec_hash=build_spec_hash, venture_repository_id=str(repo_id),
        repository_ref=repository_ref, execution_spec_id=str(spec.spec_id),
        execution_spec_created=spec.created,
    )


def execute_build(
    conn,
    action_request_id: str,
    *,
    registry: WorkerRegistry,
    worker_kind: str,
    verifier_kind: str,
    capability_scope: list,
    timeout_seconds: int,
    max_attempts: int,
    declared_inputs: Optional[dict[str, Any]] = None,
    approval_threshold: Decimal = policy.DEFAULT_APPROVAL_THRESHOLD,
    safety_mode: str = "IDEMPOTENT",
    lease_seconds: float = 300,
    clock=None,
    actor: str = "factory",
):
    """Bind build authority and dispatch the builder through the Gate 4 runtime.

    Slice 1 stops at result capture: no verification, no quality PASS, no
    completion, no lifecycle transition. The worker's result is a CLAIM only.
    Dispatch reuses ``factory.runtime.execute_action`` — the existing worker
    registry, authorization binding, claim machinery and retry — with the isolated
    venture repository as the workspace. Authorization is obtained by that runtime
    only after the build intent is frozen into the immutable execution spec above.
    """
    dispatch = prepare_build_execution(
        conn, action_request_id, worker_kind=worker_kind, verifier_kind=verifier_kind,
        capability_scope=capability_scope, timeout_seconds=timeout_seconds,
        max_attempts=max_attempts, declared_inputs=declared_inputs, actor=actor,
    )
    result = factory_runtime.execute_action(
        conn, action_request_id, registry=registry, workspace_ref=dispatch.repository_ref,
        approval_threshold=approval_threshold, safety_mode=safety_mode,
        lease_seconds=lease_seconds, clock=clock, actor=actor,
    )
    return dispatch, result


def capture_and_check_build(
    conn,
    action_request_id: str,
    *,
    substrate_release_id: str,
    execution_attempt_id: Optional[str] = None,
    workspace_root: Optional[str] = None,
    source_root=None,
    actor: str = "factory",
) -> dict:
    """Slice 2 composition: materialize the attempt's candidate output into a
    root-contained venture workspace, freeze the immutable kernel-hashed
    ``build_manifest``, and record deterministic Technical evidence.

    Evidence only — this creates NO proof, NO lifecycle transition, and NO canonical
    BUILD success. Technical PASS/FAIL is independent of the Gate 4 execution status.
    Candidate files are read from the DURABLE execution_result (worker claim); the
    kernel owns the bytes and hashes, so a worker cannot forge file identity.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT execution_attempt_id, raw_payload FROM execution_result "
            "WHERE action_request_id = %s ORDER BY received_at DESC, id DESC LIMIT 1",
            (action_request_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"no captured worker result for action {action_request_id}")
    attempt_id = execution_attempt_id or row[0]
    structured = dict((row[1] or {}).get("structured_output", {}))
    candidate_files = list(structured.get("candidate_files", []))

    workspace_root = workspace_root or ws.create_workspace()
    m = manifest_mod.capture_build_manifest(
        conn, action_request_id, execution_attempt_id=attempt_id,
        substrate_release_id=substrate_release_id, candidate_files=candidate_files,
        workspace_root=workspace_root, source_root=source_root, actor=actor,
    )
    checks = technical_mod.run_technical_checks(conn, m.build_manifest_id, workspace_root, actor=actor)
    return {"manifest": m, "workspace_root": workspace_root,
            "verdict": checks["verdict"], "checks": checks["checks"]}


def assess_build_quality(
    conn,
    action_request_id: str,
    *,
    build_manifest_id: Optional[str] = None,
    execution_attempt_id: Optional[str] = None,
    product_descriptor: Optional[dict] = None,
    review_observations=(),
    actor: str = "factory",
) -> dict:
    """Slice 3 composition: evaluate Product/Experience/Commercial/AntiGeneric for a
    captured manifest and return per-dimension + overall verdicts.

    The candidate's declared product structure is read from the durable
    execution_result (worker claim) unless supplied. Verdicts are kernel-derived;
    worker/reviewer self-reports never set them. No proof, no lifecycle, no deploy.
    """
    if build_manifest_id is None:
        attempt_id = execution_attempt_id
        if attempt_id is None:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT execution_attempt_id FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC, id DESC LIMIT 1", (action_request_id,))
                row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"no captured worker result for action {action_request_id}")
            attempt_id = row[0]
        m = manifest_mod.get_build_manifest(conn, attempt_id)
        if m is None:
            raise NotFoundError(f"no build_manifest for attempt {attempt_id}")
        build_manifest_id = m[0]

    if product_descriptor is None:
        with conn.cursor() as cur:
            if execution_attempt_id is not None:
                cur.execute(
                    "SELECT raw_payload FROM execution_result WHERE execution_attempt_id = %s "
                    "ORDER BY received_at DESC, id DESC LIMIT 1", (execution_attempt_id,))
            else:
                cur.execute(
                    "SELECT raw_payload FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC, id DESC LIMIT 1", (action_request_id,))
            row = cur.fetchone()
        structured = dict((row[0] or {}).get("structured_output", {})) if row else {}
        product_descriptor = dict(structured.get("product_manifest", {}))

    return quality_mod.run_quality_assessment(
        conn, build_manifest_id, product_descriptor=product_descriptor,
        review_observations=review_observations, actor=actor)
