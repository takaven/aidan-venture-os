"""Gate 4 Slice 2 — deterministic, DB-free verifiers (pure unit tests)."""
from __future__ import annotations

import inspect

import pytest

from aidan_core.factory import artifacts as artifacts_mod
from aidan_core.factory import verifiers as v


def _req(*, verifier_kind, contract=None, output=None, arts=()):
    return v.VerificationRequest(
        action_request_id="a", execution_attempt_id="t", verifier_kind=verifier_kind,
        expected_output_contract=contract or {}, worker_structured_output=output or {},
        artifacts=tuple(arts), spec_hash="h",
    )


def test_structured_contract_verifier_pass_and_reject():
    ver = v.StructuredContractVerifier()
    ok = ver.verify(_req(verifier_kind=ver.kind, contract={"require": {"status": "done"}}, output={"status": "done", "x": 1}))
    assert ok.verdict == "VERIFIED" and ok.verification_type == v.STRUCTURED_CONTRACT
    bad = ver.verify(_req(verifier_kind=ver.kind, contract={"require": {"status": "done"}}, output={"status": "nope"}))
    assert bad.verdict == "REJECTED"


def test_artifact_hash_verifier_independently_rehashes_content():
    ver = v.ArtifactHashVerifier()
    h = artifacts_mod.content_hash("payload-bytes")
    # The verifier re-hashes the durable CONTENT; a worker-declared content_hash is ignored.
    arts = ({"artifact_key": "out", "content": "payload-bytes", "content_hash": "worker-forged-hash"},)
    ok = ver.verify(_req(verifier_kind=ver.kind, contract={"artifact_key": "out", "expected_sha256": h}, arts=arts))
    assert ok.verdict == "VERIFIED" and ok.verification_type == v.ARTIFACT_HASH
    bad = ver.verify(_req(verifier_kind=ver.kind, contract={"artifact_key": "out", "expected_sha256": "deadbeef"}, arts=arts))
    assert bad.verdict == "REJECTED"
    # Content that does not hash to the expected value is rejected even if the
    # (ignored) declared hash matches the expected.
    tampered = ({"artifact_key": "out", "content": "tampered", "content_hash": h},)
    assert ver.verify(_req(verifier_kind=ver.kind, contract={"artifact_key": "out", "expected_sha256": h}, arts=tampered)).verdict == "REJECTED"


def test_artifact_hash_verifier_rejects_missing_artifact():
    ver = v.ArtifactHashVerifier()
    res = ver.verify(_req(verifier_kind=ver.kind, contract={"artifact_key": "missing", "expected_sha256": "x"}, arts=()))
    assert res.verdict == "REJECTED"


def test_worker_forged_verdict_field_is_inert():
    # A worker stuffing 'verdict'/'verified' into its structured output changes nothing:
    # the structured-contract verifier only checks the immutable required map.
    ver = v.StructuredContractVerifier()
    res = ver.verify(_req(
        verifier_kind=ver.kind, contract={"require": {"status": "done"}},
        output={"status": "nope", "verdict": "VERIFIED", "verified": True, "proof": "yes"},
    ))
    assert res.verdict == "REJECTED"


def test_registry_and_defaults():
    reg = v.default_registry()
    assert isinstance(reg.get("structured-contract"), v.StructuredContractVerifier)
    assert isinstance(reg.get("artifact-hash"), v.ArtifactHashVerifier)
    with pytest.raises(KeyError):
        reg.get("llm-reviewer")


def test_verifier_is_pure_no_db_connection():
    # verify takes only the request — no cursor/connection.
    assert list(inspect.signature(v.StructuredContractVerifier().verify).parameters) == ["request"]
    assert list(inspect.signature(v.ArtifactHashVerifier().verify).parameters) == ["request"]


def test_evidence_hash_is_deterministic():
    ver = v.StructuredContractVerifier()
    r1 = ver.verify(_req(verifier_kind=ver.kind, contract={"require": {"a": 1}}, output={"a": 1}))
    r2 = ver.verify(_req(verifier_kind=ver.kind, contract={"require": {"a": 1}}, output={"a": 1}))
    assert r1.evidence_hash == r2.evidence_hash and r1.verdict == "VERIFIED"
