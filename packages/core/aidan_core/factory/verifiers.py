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

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from ..actions import canonical_payload_hash

# Machine verification types recorded on the canonical proof receipt.
STRUCTURED_CONTRACT = "STRUCTURED_CONTRACT"
ARTIFACT_HASH = "ARTIFACT_HASH"
TEST_EXECUTION = "TEST_EXECUTION"


@dataclass(frozen=True)
class VerificationRequest:
    action_request_id: str
    execution_attempt_id: str
    verifier_kind: str
    expected_output_contract: dict[str, Any]
    worker_structured_output: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]
    spec_hash: str
    # The exact CAPTURED execution_result.external_result_id the Proof Receipt will bind to. A
    # verifier that proves a provider object MUST prove the object named by THIS canonical id,
    # never a divergent value taken only from the (untrusted) worker structured output.
    external_result_id: str = ""
    # Machine-owned test evidence produced by the TRUSTED sandbox runner during the trusted
    # verification phase (never by the worker). A ``SandboxEvidence.to_dict()`` for TEST_EXECUTION.
    test_evidence: Optional[dict[str, Any]] = None
    # Kernel-computed hash of the durable candidate the runner actually executed, so the pure
    # verifier can bind the evidence to the captured candidate (not a swapped one).
    candidate_content_hash: str = ""


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
                content = a.get("content")
                # Independently RE-HASH the durable content — never trust a stored
                # or worker-declared hash (a persisted hash cannot verify itself).
                if isinstance(content, str):
                    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
                break
        ok = actual is not None and expected is not None and actual == expected
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            _evidence_hash(request, {"artifact_key": key, "actual_hash": actual}),
            detail={"artifact_key": key, "expected": expected, "actual": actual},
        )


class TestExecutionVerifier:
    """VERIFIED iff TRUSTED sandbox evidence proves the frozen test PASSED on the captured
    candidate. Pure and deterministic over already-collected evidence — it runs NO
    subprocess itself (the trusted runner in ``factory.test_execution`` executed the
    sandbox during the trusted verification phase and handed the machine-owned evidence in
    via ``request.test_evidence``). Worker self-report is structurally irrelevant: a worker
    ``reported_outcome``/``structured_output``/``TEST_REPORT`` artifact never reaches this
    verifier. Success requires the evidence to be bound to (a) the frozen test identity, (b)
    the trusted runner identity, and (c) the exact captured candidate, plus a clean terminal
    machine result.
    """

    kind = "test-execution"
    verification_type = TEST_EXECUTION

    def verify(self, request: VerificationRequest) -> VerificationResult:
        ev = request.test_evidence or {}
        contract = (request.expected_output_contract or {}).get("test_execution", {})
        exp_test_sha = contract.get("test_sha256")
        min_tests = contract.get("min_tests", 1)
        exp_runner_kind = contract.get("runner_kind")
        exp_runner_version = contract.get("runner_version")

        reasons = []
        if request.test_evidence is None:
            reasons.append("no_evidence")
        # (a) frozen test identity — evidence must be for THE immutable harness.
        if not exp_test_sha or ev.get("test_sha256") != exp_test_sha:
            reasons.append("test_sha256")
        # (b) trusted runner identity, when the contract pins it.
        if exp_runner_kind is not None and ev.get("runner_kind") != exp_runner_kind:
            reasons.append("runner_kind")
        if exp_runner_version is not None and str(ev.get("runner_version")) != str(exp_runner_version):
            reasons.append("runner_version")
        # (c) candidate binding — evidence must be for the captured candidate, not a swap.
        if not request.candidate_content_hash or ev.get("candidate_sha256") != request.candidate_content_hash:
            reasons.append("candidate_sha256")
        # terminal machine result — completed, clean exit, harness PASS, all tests passed.
        if ev.get("terminal_state") != "COMPLETED":
            reasons.append("terminal_state")
        if ev.get("exit_code") != 0:
            reasons.append("exit_code")
        if ev.get("harness_result") != "PASS":
            reasons.append("harness_result")
        total, passed = ev.get("tests_total"), ev.get("tests_passed")
        if not isinstance(total, int) or total < min_tests or passed != total:
            reasons.append("test_counts")

        ok = not reasons
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            _evidence_hash(request, {"evidence": ev, "candidate": request.candidate_content_hash}),
            detail={"reasons": reasons},
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
    reg.register(TestExecutionVerifier())
    return reg
