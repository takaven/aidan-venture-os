"""Deterministic deployment verifier (Gate 6 Slice 2).

A ``DeploymentReleaseVerifier`` is an ordinary Gate-4 ``Verifier`` (no DB connection,
no lifecycle/spec/status mutation). It independently establishes observed deployment
state from the controlled local target — a worker's ``deployed=true`` claim is inert.
It is VERIFIED only when EVERY required check passes (no score, no compensation). The
trusted kernel converts a VERIFIED result into the ONE canonical ``proof_receipt`` via
the existing proof authority — there is no second proof system.
"""
from __future__ import annotations

from typing import Any

from ..actions import canonical_payload_hash
from ..factory.verifiers import VerificationRequest, VerificationResult, VerifierRegistry
from . import checks as checks_mod

DEPLOYMENT_RELEASE = "DEPLOYMENT_RELEASE"


class DeploymentReleaseVerifier:
    """VERIFIED iff every required deployment check passes over the actual target."""

    kind = "deployment-release"
    verification_type = DEPLOYMENT_RELEASE

    def verify(self, request: VerificationRequest) -> VerificationResult:
        contract: dict[str, Any] = dict((request.expected_output_contract or {}).get("deployment", {}))
        target_path = contract.get("target_path")
        results = checks_mod.evaluate(
            target_path,
            venture_id=contract.get("venture_id"),
            deployment_target_id=contract.get("deployment_target_id"),
            expected_tree_hash=contract.get("candidate_tree_hash"),
            release_contract=contract.get("release_contract", {}),
        )
        ok = all(r.result == "PASS" for r in results)
        evidence = {
            "release_candidate_id": contract.get("release_candidate_id"),
            "release_hash": contract.get("release_hash"),
            "deployment_target_id": contract.get("deployment_target_id"),
            "checks": [[r.name, r.result] for r in results],
        }
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            canonical_payload_hash({"attempt": str(request.execution_attempt_id), **evidence}),
            detail={"checks": [{"name": r.name, "result": r.result, "detail": r.detail} for r in results]},
        )


def deploy_verifier_registry() -> VerifierRegistry:
    """A verifier registry containing the deployment verifier (passed to verify_and_complete)."""
    reg = VerifierRegistry()
    reg.register(DeploymentReleaseVerifier())
    return reg
