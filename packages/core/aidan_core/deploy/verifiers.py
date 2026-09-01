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
from .observe import LocalTargetObserver

DEPLOYMENT_RELEASE = "DEPLOYMENT_RELEASE"


class DeploymentReleaseVerifier:
    """VERIFIED iff every required deployment check passes over the OBSERVED target.

    The observed deployment state is read back through a provider-neutral
    :class:`~aidan_core.deploy.observe.DeploymentObserver`. ``observer_factory(contract) ->
    DeploymentObserver`` selects the read-back; it defaults to the controlled LOCAL target
    (Slices 1-3). A future real deployment injects a provider observer that reads the SAME
    observation shape back from the external target — the checks and identity hash are unchanged.
    A worker's ``deployed=true`` claim is never consulted, on either path.
    """

    kind = "deployment-release"
    verification_type = DEPLOYMENT_RELEASE

    def __init__(self, observer_factory=None):
        self._observer_factory = observer_factory or (lambda c: LocalTargetObserver(c.get("target_path")))

    def verify(self, request: VerificationRequest) -> VerificationResult:
        contract: dict[str, Any] = dict((request.expected_output_contract or {}).get("deployment", {}))
        observation = self._observer_factory(contract).observe()
        results = checks_mod.evaluate_observation(
            observation,
            venture_id=contract.get("venture_id"),
            deployment_target_id=contract.get("deployment_target_id"),
            expected_tree_hash=contract.get("candidate_tree_hash"),
            release_contract=contract.get("release_contract", {}),
        )
        ok = all(r.result == "PASS" for r in results)
        # Hashed evidence identity is UNCHANGED from Slices 1-3 (read-back is a seam, not a new
        # proof field): the deployment proof_receipt.evidence_hash formula is preserved exactly.
        evidence = {
            "release_candidate_id": contract.get("release_candidate_id"),
            "release_hash": contract.get("release_hash"),
            "deployment_target_id": contract.get("deployment_target_id"),
            "checks": [[r.name, r.result] for r in results],
        }
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            canonical_payload_hash({"attempt": str(request.execution_attempt_id), **evidence}),
            # bounded, provider-neutral read-back evidence surfaced for observability only (not hashed)
            detail={"read_back_contact": observation.contact,
                    "checks": [{"name": r.name, "result": r.result, "detail": r.detail} for r in results]},
        )


def deploy_verifier_registry() -> VerifierRegistry:
    """A verifier registry containing the deployment verifier (passed to verify_and_complete)."""
    reg = VerifierRegistry()
    reg.register(DeploymentReleaseVerifier())
    return reg
