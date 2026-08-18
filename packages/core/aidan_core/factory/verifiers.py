"""Deterministic, provider-neutral verification.

A Verifier is a pure deterministic function over CANONICAL inputs (immutable
expected-output contract, captured worker result, captured artifacts). It never
receives a database connection and cannot mutate policy, approval, lifecycle, the
spec, or canonical status. It returns a transient ``VerificationResult`` — NOT a
canonical proof receipt. The trusted kernel converts a VERIFIED result into the
one canonical ``proof_receipt`` via the existing proof authority.

Worker self-report and artifact existence are not verification: only a
deterministic verifier's VERIFIED result (over independently established
evidence) can permit success. No LLM/subjective verifier participates in
consequential success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..actions import canonical_payload_hash

# Machine verification types recorded on the canonical proof receipt.
STRUCTURED_CONTRACT = "STRUCTURED_CONTRACT"
ARTIFACT_HASH = "ARTIFACT_HASH"


@dataclass(frozen=True)
class VerificationRequest:
    action_request_id: str
    execution_attempt_id: str
    verifier_kind: str
    expected_output_contract: dict[str, Any]
    worker_structured_output: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]
    spec_hash: str


@dataclass(frozen=True)
class VerificationResult:
    verifier_kind: str
    verdict: str            # 'VERIFIED' or 'REJECTED'
    verification_type: str
    evidence_hash: str
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Verifier(Protocol):
    kind: str
    verification_type: str

    def verify(self, request: VerificationRequest) -> VerificationResult:  # pragma: no cover - protocol
        ...


def _evidence_hash(request: VerificationRequest, actual: dict) -> str:
    return canonical_payload_hash({
        "attempt": str(request.execution_attempt_id),
        "verifier_kind": request.verifier_kind,
        "spec_hash": request.spec_hash,
        "expected": request.expected_output_contract,
        "actual": actual,
    })


class StructuredContractVerifier:
    """VERIFIED iff the worker's structured output satisfies every required key/value
    in the immutable expected contract's ``require`` map. Deterministic subset match."""

    kind = "structured-contract"
    verification_type = STRUCTURED_CONTRACT

    def verify(self, request: VerificationRequest) -> VerificationResult:
        require = (request.expected_output_contract or {}).get("require", {})
        out = request.worker_structured_output or {}
        ok = all(out.get(k) == v for k, v in require.items())
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            _evidence_hash(request, {"structured_output": out}),
            detail={"required": require, "satisfied": ok},
        )


class ArtifactHashVerifier:
    """VERIFIED iff an artifact with the contract's ``artifact_key`` exists and its
    KERNEL-COMPUTED content hash equals the contract's ``expected_sha256``.

    The stored content hash was computed by the kernel at capture, so a
    worker-declared hash cannot influence this verdict.
    """

    kind = "artifact-hash"
    verification_type = ARTIFACT_HASH

    def verify(self, request: VerificationRequest) -> VerificationResult:
        contract = request.expected_output_contract or {}
        key = contract.get("artifact_key")
        expected = contract.get("expected_sha256")
        actual = None
        for a in request.artifacts:
            if a.get("artifact_key") == key:
                actual = a.get("content_hash")
                break
        ok = actual is not None and expected is not None and actual == expected
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            _evidence_hash(request, {"artifact_key": key, "actual_hash": actual}),
            detail={"artifact_key": key, "expected": expected, "actual": actual},
        )


class VerifierRegistry:
    """Smallest replaceability mechanism: verifier_kind -> Verifier. No marketplace."""

    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}

    def register(self, verifier: Verifier) -> None:
        self._verifiers[verifier.kind] = verifier

    def get(self, verifier_kind: str) -> Verifier:
        try:
            return self._verifiers[verifier_kind]
        except KeyError:
            raise KeyError(f"no verifier registered for kind {verifier_kind!r}") from None


def default_registry() -> VerifierRegistry:
    reg = VerifierRegistry()
    reg.register(StructuredContractVerifier())
    reg.register(ArtifactHashVerifier())
    return reg
