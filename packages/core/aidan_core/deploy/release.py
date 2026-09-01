"""Immutable, quality-qualified release candidates (Gate 6 Slice 1).

A ``release_candidate`` freezes the exact Gate-5-quality-passed build candidate AND
the exact deployment intent before any deployment execution authority exists. It is
1:1 with the deploy ActionRequest and immutable. A Gate-5 overall PASS is a hard
PREREQUISITE, reloaded from PostgreSQL for the exact build_manifest — never a caller
claim, worker result, or reviewer observation.

``release_hash`` binds the COMPLETE immutable release intent — candidate identity
(build manifest + candidate tree hash + build spec), target identity (id +
environment + provider + ref), and the normalized release contract — so a changed
target/environment/contract can never masquerade as the same release.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..build import quality as quality_mod
from ..errors import DeployAuthorityError, IdempotencyConflictError, NotFoundError
from . import target as target_mod

# Bounded release-contract vocabulary (deployment-relevant intent only; no secrets). The
# real-external-deploy keys freeze the immutable artifact identity + provider runtime intent into
# release_hash: expected_artifact_identity (the trusted-tool-derived OCI digest — the release
# authority a provider read-back is compared against), image_ref (the digest-pinned image the worker
# deploys, nothing else), region, required_state. None of these is a secret.
RELEASE_CONTRACT_KEYS = frozenset({
    "runtime_kind", "entry_artifact", "health_contract", "required_config",
    "expected_artifact_identity", "image_ref", "region", "required_state",
})


@dataclass(frozen=True)
class ReleaseResult:
    release_candidate_id: str
    release_hash: str
    created: bool


def compute_release_hash(
    *, venture_id, build_manifest_id, build_spec_id, candidate_tree_hash,
    deployment_target_id, environment, provider_kind, target_ref, release_contract,
) -> str:
    """Deterministic identity of the COMPLETE release intent (candidate + target + contract)."""
    return canonical_payload_hash({
        "venture_id": str(venture_id),
        "build_manifest_id": str(build_manifest_id),
        "build_spec_id": str(build_spec_id),
        "candidate_tree_hash": candidate_tree_hash,
        "deployment_target_id": str(deployment_target_id),
        "environment": environment,
        "provider_kind": provider_kind,
        "target_ref": target_ref,
        "release_contract": release_contract,
    })


def _require_quality_qualified(conn, build_manifest_id):
    """Reload Gate-5 quality from PostgreSQL; every dimension must PASS."""
    overall = quality_mod.overall_verdict(conn, build_manifest_id)
    if overall != "PASS":
        raise DeployAuthorityError(
            f"build_manifest {build_manifest_id} is not Gate-5 quality-qualified (overall={overall}); "
            "a release candidate requires Technical+Product+Experience+Commercial+AntiGeneric PASS"
        )


def create_release_candidate(
    conn,
    action_request_id: str,
    *,
    build_manifest_id: str,
    deployment_target_id: str,
    release_contract: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> ReleaseResult:
    """Freeze the immutable release_candidate for a deploy ActionRequest. Idempotent.

    Requires the exact build_manifest to be Gate-5 quality-qualified and the target to
    belong to the same venture as the action. Identical re-freeze converges; any
    materially changed release intent is a deterministic :class:`IdempotencyConflictError`.
    """
    release_contract = release_contract or {}
    unknown = sorted(set(release_contract) - RELEASE_CONTRACT_KEYS)
    if unknown:
        raise ValueError(f"unknown release_contract keys {unknown}; allowed: {sorted(RELEASE_CONTRACT_KEYS)}")

    with db.transaction(conn) as cur:
        cur.execute("SELECT venture_id, action_type FROM action_request WHERE id = %s", (action_request_id,))
        arow = cur.fetchone()
        if arow is None:
            raise NotFoundError(f"action_request {action_request_id} does not exist")
        venture_id, action_type = arow
        if action_type != "deploy":
            raise DeployAuthorityError(
                f"action {action_request_id} is a {action_type!r} action, not 'deploy'; "
                "only a deploy ActionRequest may own a release candidate"
            )

        cur.execute(
            "SELECT venture_id, build_spec_id, candidate_tree_hash FROM build_manifest WHERE id = %s",
            (build_manifest_id,),
        )
        mrow = cur.fetchone()
        if mrow is None:
            raise NotFoundError(f"build_manifest {build_manifest_id} does not exist")
        m_venture, build_spec_id, candidate_tree_hash = mrow
        if str(m_venture) != str(venture_id):
            raise DeployAuthorityError("build_manifest belongs to a different venture than the deploy action")

        trow = target_mod.get_deployment_target(conn, deployment_target_id)
        if trow is None:
            raise NotFoundError(f"deployment_target {deployment_target_id} does not exist")
        if str(trow[1]) != str(venture_id):
            raise DeployAuthorityError("deployment_target belongs to a different venture than the deploy action")
        environment, provider_kind, target_ref = trow[2], trow[3], trow[4]

        _require_quality_qualified(conn, build_manifest_id)

        release_hash = compute_release_hash(
            venture_id=venture_id, build_manifest_id=build_manifest_id, build_spec_id=build_spec_id,
            candidate_tree_hash=candidate_tree_hash, deployment_target_id=deployment_target_id,
            environment=environment, provider_kind=provider_kind, target_ref=target_ref,
            release_contract=release_contract,
        )

        cur.execute("SELECT id, release_hash FROM release_candidate WHERE action_request_id = %s",
                    (action_request_id,))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != release_hash:
                raise IdempotencyConflictError(
                    f"release_candidate for action {action_request_id} already exists with "
                    "materially different release intent"
                )
            return ReleaseResult(existing[0], release_hash, created=False)

        cur.execute(
            """
            INSERT INTO release_candidate
                (venture_id, action_request_id, build_manifest_id, build_spec_id, deployment_target_id,
                 candidate_tree_hash, release_contract, release_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, action_request_id, build_manifest_id, build_spec_id, deployment_target_id,
             candidate_tree_hash, Json(release_contract), release_hash),
        )
        release_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="deploy.release_candidate_frozen", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"release_candidate_id": str(release_id), "release_hash": release_hash},
        )
    return ReleaseResult(release_id, release_hash, created=True)


def get_release_candidate(conn, action_request_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, action_request_id, build_manifest_id, build_spec_id, "
            "deployment_target_id, candidate_tree_hash, release_contract, release_hash, created_at "
            "FROM release_candidate WHERE action_request_id = %s",
            (action_request_id,),
        )
        return cur.fetchone()
