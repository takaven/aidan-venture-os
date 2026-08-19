"""Immutable execution specifications bound 1:1 to a governed ActionRequest.

An execution_spec freezes the executable work, capability boundary and future
verification contract for an ActionRequest. It is created by trusted
kernel/operator integration BEFORE the authorization used for dispatch — never
inferred from worker output — and it is immutable: correcting the task means a
new governed ActionRequest/spec, never a mutation. The Gate 4 runtime re-checks
authorization against the frozen spec, so authorization that predates the spec
cannot authorize its dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import (
    BuildAuthorityError,
    DeployAuthorityError,
    IdempotencyConflictError,
    NotFoundError,
)

# Finite capability vocabulary (mirrors the DB CHECK). DEPLOY_CANDIDATE (Gate 6/0018)
# authorizes a bounded deployment of an already-frozen release candidate.
CAPABILITIES = frozenset(
    {"READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "RUN_TESTS", "PRODUCE_PATCH",
     "READ_DECLARED_INPUTS", "DEPLOY_CANDIDATE"}
)


@dataclass(frozen=True)
class SpecResult:
    spec_id: str
    spec_hash: str
    created: bool


def _assert_build_action_binding(cur, action_request_id, venture_id, task_payload):
    """Single authoritative Gate 5 build-authority boundary at spec creation.

    If the ActionRequest is a CANONICAL BUILD action (it is the resulting action of
    a BUILD ``investment_decision_record``), its execution spec may only be created
    when it is bound to the immutable Gate 5 ``build_spec`` and the venture's
    registered ``venture_repository`` — the task payload must carry the exact
    build_spec id + hash and a venture-owned repository. This holds regardless of
    which trusted Factory caller creates the spec (the ``build.runtime`` helper is
    convenience, not the boundary), so a BUILD action can never be executed from
    free-form prose. Non-BUILD actions are unaffected: the generic Gate 4 path is
    unchanged. Because ``execute_action`` only ever dispatches an already-created
    spec, guarding creation makes dispatch safe for every caller too.
    """
    cur.execute(
        "SELECT 1 FROM investment_decision_record "
        "WHERE resulting_action_id = %s AND decision = 'BUILD' LIMIT 1",
        (action_request_id,),
    )
    if cur.fetchone() is None:
        return  # not a canonical BUILD action — generic Gate 4, no build binding required

    cur.execute(
        "SELECT id, spec_hash FROM build_spec WHERE action_request_id = %s",
        (action_request_id,),
    )
    bs = cur.fetchone()
    if bs is None:
        raise BuildAuthorityError(
            f"action {action_request_id} is a canonical BUILD action but has no frozen "
            "build_spec; a BUILD execution spec cannot be created from free-form intent"
        )
    build_spec_id, build_spec_hash = bs
    block = dict((task_payload or {}).get("build", {}))
    if str(block.get("build_spec_id")) != str(build_spec_id):
        raise BuildAuthorityError(
            "execution spec for a BUILD action must bind the canonical build_spec id"
        )
    if block.get("build_spec_hash") != build_spec_hash:
        raise BuildAuthorityError(
            "execution spec build_spec_hash does not match the canonical build_spec"
        )
    # The bound repository must be the venture's own registered repository.
    cur.execute("SELECT id FROM venture_repository WHERE venture_id = %s", (venture_id,))
    repo = cur.fetchone()
    if repo is None or str(repo[0]) != str(block.get("venture_repository_id")):
        raise BuildAuthorityError(
            "execution spec for a BUILD action must bind the venture's registered repository"
        )


def _assert_deploy_action_binding(cur, action_request_id, venture_id, action_type, task_payload):
    """Single authoritative Gate 6 deploy-authority boundary at spec creation.

    A canonical deploy action (``action_type = 'deploy'`` OR an action that already owns
    a ``release_candidate``) may only get an execution spec when its task payload binds
    the exact immutable, quality-qualified ``release_candidate`` (id + release_hash) and
    its venture-owned ``deployment_target`` — regardless of which trusted Factory caller
    creates the spec. So a deploy action can never be executed from free-form intent, and
    the worker can never choose what/where to deploy. Non-deploy actions are unaffected.
    """
    cur.execute(
        "SELECT id, release_hash, deployment_target_id, venture_id "
        "FROM release_candidate WHERE action_request_id = %s",
        (action_request_id,),
    )
    rc = cur.fetchone()
    if action_type != "deploy" and rc is None:
        return  # not a canonical deploy action — generic path, no release binding required
    if rc is None:
        raise DeployAuthorityError(
            f"action {action_request_id} is a canonical deploy action but has no frozen "
            "release_candidate; a deploy execution spec cannot be created from free-form intent"
        )
    rc_id, rc_hash, rc_target, rc_venture = rc
    if str(rc_venture) != str(venture_id):
        raise DeployAuthorityError("release_candidate belongs to a different venture than the action")
    block = dict((task_payload or {}).get("deploy", {}))
    if str(block.get("release_candidate_id")) != str(rc_id):
        raise DeployAuthorityError("execution spec for a deploy action must bind the canonical release_candidate id")
    if block.get("release_hash") != rc_hash:
        raise DeployAuthorityError("execution spec release_hash does not match the canonical release_candidate")
    if str(block.get("deployment_target_id")) != str(rc_target):
        raise DeployAuthorityError("execution spec must bind the release_candidate's deployment target")


def compute_spec_hash(
    *, worker_kind: str, task_payload: dict, expected_output_contract: dict,
    verifier_kind: str, timeout_seconds: int, max_attempts: int, capability_scope: list,
) -> str:
    """Deterministic identity over the spec's executable authority.

    Covers everything that defines what work is authorized and how it will be
    judged. Excludes DB-generated ids and timestamps. Capabilities are sorted so
    ordering is not semantic. Ints are used directly (no Decimal normalization).
    """
    return canonical_payload_hash({
        "worker_kind": worker_kind,
        "task_payload": task_payload,
        "expected_output_contract": expected_output_contract,
        "verifier_kind": verifier_kind,
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
        "capability_scope": sorted(capability_scope),
    })


def create_execution_spec(
    conn,
    action_request_id: str,
    *,
    worker_kind: str,
    verifier_kind: str,
    timeout_seconds: int,
    max_attempts: int,
    capability_scope: list,
    task_payload: Optional[dict[str, Any]] = None,
    expected_output_contract: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> SpecResult:
    """Freeze the execution spec for a governed ActionRequest. Idempotent per action.

    Reuse with an identical spec converges; a changed spec under the same
    ActionRequest is a hard conflict (never a silent mutation).
    """
    if not worker_kind:
        raise ValueError("worker_kind is required")
    if not verifier_kind:
        raise ValueError("verifier_kind is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    caps = list(capability_scope or [])
    unknown = set(caps) - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown capabilities {sorted(unknown)}; allowed: {sorted(CAPABILITIES)}")

    task_payload = task_payload or {}
    expected_output_contract = expected_output_contract or {}
    task_hash = canonical_payload_hash(task_payload)
    spec_hash = compute_spec_hash(
        worker_kind=worker_kind, task_payload=task_payload,
        expected_output_contract=expected_output_contract, verifier_kind=verifier_kind,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts, capability_scope=caps,
    )

    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id, action_type FROM action_request WHERE id = %s", (action_request_id,))
        arow = cur.fetchone()
        if arow is None:
            raise NotFoundError(f"action_request {action_request_id} does not exist")
        venture_id, action_type = arow

        # Authoritative Gate 5 boundary: a canonical BUILD action's spec must be
        # bound to its immutable build_spec + venture repository (any caller).
        _assert_build_action_binding(cur, action_request_id, venture_id, task_payload)
        # Authoritative Gate 6 boundary: a canonical deploy action's spec must be
        # bound to its immutable quality-qualified release_candidate + target (any caller).
        _assert_deploy_action_binding(cur, action_request_id, venture_id, action_type, task_payload)

        cur.execute(
            "SELECT id, spec_hash FROM execution_spec WHERE action_request_id = %s",
            (action_request_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != spec_hash:
                raise IdempotencyConflictError(
                    f"execution_spec for action {action_request_id} already exists with different content"
                )
            return SpecResult(existing[0], spec_hash, created=False)

        cur.execute(
            """
            INSERT INTO execution_spec
                (action_request_id, venture_id, worker_kind, task_payload, task_hash,
                 expected_output_contract, verifier_kind, timeout_seconds, max_attempts,
                 capability_scope, spec_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (action_request_id, venture_id, worker_kind, Json(task_payload), task_hash,
             Json(expected_output_contract), verifier_kind, timeout_seconds, max_attempts,
             caps, spec_hash),
        )
        spec_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="factory.execution_spec_created", actor=actor, venture_id=venture_id,
            action_id=action_request_id, payload={"spec_id": str(spec_id), "spec_hash": spec_hash},
        )
    return SpecResult(spec_id, spec_hash, created=True)


def get_execution_spec(conn, action_request_id: str):
    """Return the frozen spec row for an action, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, action_request_id, venture_id, worker_kind, task_payload, task_hash, "
            "expected_output_contract, verifier_kind, timeout_seconds, max_attempts, "
            "capability_scope, spec_hash, created_at FROM execution_spec WHERE action_request_id = %s",
            (action_request_id,),
        )
        return cur.fetchone()
