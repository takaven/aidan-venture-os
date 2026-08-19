"""Canonical deployment state projection (Gate 6 Slice 3).

There is NO ``deployment_record`` table: the existing immutable chain already answers
every deployment-truth query —

    release_candidate → deploy ActionRequest → execution_attempt
        → deployment-specific VERIFIED proof_receipt (verification_type = DEPLOYMENT_RELEASE)
        → deployment_target

so a successful deployment (and the latest one per venture/target) is derived from
canonical state, never stored as mutable "current release".

``promote_verified_deployment`` is the ONE trusted composition that converts a
deterministically VERIFIED deployment proof into the smallest permitted lifecycle
outcome (``BUILDING → OPERATING``) via the existing lifecycle kernel. A worker/verifier
claim never transitions lifecycle; a generic worker-execution proof is insufficient;
cross-venture/wrong-attempt proofs are rejected; and a kill switch engaged after the
proof blocks promotion. OPERATING means "verified deployed runtime" — NOT market
success (that is Gate 7).
"""
from __future__ import annotations

from typing import Optional

from .. import db, killswitch, lifecycle
from ..build import quality as quality_mod
from ..errors import DeployAuthorityError, ExecutionBlockedError, NotFoundError

_DEPLOYMENT_VERIFICATION = "DEPLOYMENT_RELEASE"


def verified_deployment(conn, deploy_action_request_id: str) -> Optional[dict]:
    """Reconstruct the successful deployment for a deploy action from canonical state,
    or None if there is no deployment-specific VERIFIED proof."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, deployment_target_id, build_manifest_id, release_hash "
            "FROM release_candidate WHERE action_request_id = %s", (deploy_action_request_id,))
        rc = cur.fetchone()
        if rc is None:
            return None
        cur.execute(
            "SELECT id, execution_attempt_id FROM proof_receipt WHERE action_request_id = %s "
            "AND result = 'VERIFIED' AND verification_type = %s ORDER BY created_at DESC LIMIT 1",
            (deploy_action_request_id, _DEPLOYMENT_VERIFICATION))
        pr = cur.fetchone()
        if pr is None:
            return None
    return {
        "release_candidate_id": str(rc[0]), "venture_id": str(rc[1]),
        "deployment_target_id": str(rc[2]), "build_manifest_id": str(rc[3]),
        "release_hash": rc[4], "proof_receipt_id": str(pr[0]),
        "execution_attempt_id": str(pr[1]), "action_request_id": str(deploy_action_request_id),
    }


def latest_verified_deployment(conn, venture_id: str, deployment_target_id: str) -> Optional[dict]:
    """The most recent verified deployment for a venture/target, derived deterministically."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rc.action_request_id, rc.id, pr.id, pr.execution_attempt_id, rc.release_hash "
            "FROM release_candidate rc "
            "JOIN proof_receipt pr ON pr.action_request_id = rc.action_request_id "
            "  AND pr.result = 'VERIFIED' AND pr.verification_type = %s "
            "WHERE rc.venture_id = %s AND rc.deployment_target_id = %s "
            "ORDER BY pr.created_at DESC, pr.id DESC LIMIT 1",
            (_DEPLOYMENT_VERIFICATION, venture_id, deployment_target_id))
        row = cur.fetchone()
    if row is None:
        return None
    return {"action_request_id": str(row[0]), "release_candidate_id": str(row[1]),
            "proof_receipt_id": str(row[2]), "execution_attempt_id": str(row[3]), "release_hash": row[4]}


def promote_verified_deployment(conn, deploy_action_request_id: str, *, actor: str = "factory") -> dict:
    """Proof-gated BUILDING → OPERATING projection. Idempotent; trusted kernel only.

    Reloads canonical state and requires a deployment-specific VERIFIED proof bound to
    this exact venture/action/attempt/release; rechecks the kill switch at promotion
    time; then transitions via the sole lifecycle authority. A worker/verifier claim
    cannot trigger it, and it never mutates the proof or the release candidate.
    """
    dep = verified_deployment(conn, deploy_action_request_id)
    if dep is None:
        raise DeployAuthorityError(
            f"action {deploy_action_request_id} has no VERIFIED deployment proof; "
            "lifecycle cannot be promoted to OPERATING"
        )
    venture_id = dep["venture_id"]

    with conn.cursor() as cur:  # provenance: the proof's attempt belongs to this exact action
        cur.execute("SELECT 1 FROM execution_attempt WHERE id = %s AND action_request_id = %s",
                    (dep["execution_attempt_id"], deploy_action_request_id))
        if cur.fetchone() is None:
            raise DeployAuthorityError("deployment proof attempt does not belong to this deploy action")
    if quality_mod.overall_verdict(conn, dep["build_manifest_id"]) != "PASS":
        raise DeployAuthorityError("release build is no longer Gate-5 quality-qualified")

    with db.transaction(conn) as cur:
        ks = killswitch.effective_state(cur, venture_id)
        if ks["global"] or ks["venture"]:
            raise ExecutionBlockedError("kill switch engaged; verified deployment cannot be promoted")
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s FOR UPDATE", (venture_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"venture {venture_id} does not exist")
        current = row[0]
        if current == "OPERATING":
            return {"outcome": "already_operating", "venture_id": venture_id, "state": "OPERATING",
                    "proof_receipt_id": dep["proof_receipt_id"]}
        lifecycle.transition_cur(cur, venture_id, "OPERATING", actor=actor,
                                 reason=f"verified_deployment:{dep['proof_receipt_id']}")
    return {"outcome": "promoted", "venture_id": venture_id, "from": current, "state": "OPERATING",
            "proof_receipt_id": dep["proof_receipt_id"]}
