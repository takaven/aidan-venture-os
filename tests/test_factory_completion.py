"""Gate 4 Slice 2 — deterministic verification -> canonical proof -> proof-gated
completion. Worker self-report and artifact existence never decide success.
"""
from __future__ import annotations

import inspect

import psycopg
import pytest

from aidan_core import execution, proof
from aidan_core.errors import ExecutionBlockedError
from aidan_core.factory import artifacts as artifacts_mod, runtime
from aidan_core.factory.verifiers import ARTIFACT_HASH, STRUCTURED_CONTRACT, default_registry

from conftest import setup_action
from factory_fakes import FakeWorkerA, FakeWorkerB, registry_with, spec_action


def _latest_proof(conn, aid):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT result, verification_type, execution_attempt_id FROM proof_receipt "
            "WHERE action_request_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (aid,),
        )
        return cur.fetchone()


def _struct(migrated, slug, *, worker_outcome, output, require=None):
    vid, aid, _ = spec_action(
        migrated, slug, verifier_kind="structured-contract",
        expected_output_contract={"require": require or {"status": "done"}},
    )
    runtime.execute_action(
        migrated, aid, registry=registry_with(FakeWorkerA(reported_outcome=worker_outcome, structured_output=output)))
    return aid


def _artifact_action(migrated, slug, *, content, expected):
    vid, aid, _ = spec_action(
        migrated, slug, verifier_kind="artifact-hash",
        expected_output_contract={"artifact_key": "out", "expected_sha256": expected},
    )
    decl = {"artifact_key": "out", "artifact_type": "STRUCTURED_RESULT", "ref": "out", "content": content}
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA(artifacts=[decl])))
    return aid


# --------------------------------------------------------------------------
# Structured-contract verification.
# --------------------------------------------------------------------------
def test_correct_structured_contract_succeeds(migrated):
    aid = _struct(migrated, "cp-ok", worker_outcome="success", output={"status": "done"})
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert execution.get_status(migrated, aid) == "SUCCEEDED"
    result, vtype, attempt = _latest_proof(migrated, aid)
    assert result == "VERIFIED" and vtype == STRUCTURED_CONTRACT and attempt is not None


def test_wrong_structured_contract_is_rejected_and_retryable(migrated):
    # Default max_attempts=3: a wrong deterministic contract is REJECTED and, with
    # retries remaining, is a retryable attempt failure — not action failure.
    aid = _struct(migrated, "cp-bad", worker_outcome="success", output={"status": "nope"})
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.verified is False
    # Rejected proof/history preserved; no VERIFIED proof; no canonical SUCCESS.
    assert _latest_proof(migrated, aid)[0] == "FAILED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'", (aid,))
        assert cur.fetchone()[0] == 0
    # The action remains retryable (attempts remain), not terminal.
    assert execution.get_status(migrated, aid) == "PENDING"


def test_worker_success_but_verifier_rejects_gives_no_success(migrated):
    # Worker claims success; deterministic verifier disagrees. Verifier wins.
    aid = _struct(migrated, "cp-selfreport", worker_outcome="success", output={"status": "actually-broken"})
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status != "SUCCEEDED"


def test_worker_non_success_but_verifier_passes_succeeds(migrated):
    # Worker is uncertain; deterministic evidence satisfies the contract. Verifier wins.
    aid = _struct(migrated, "cp-uncertain", worker_outcome="uncertain", output={"status": "done"})
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True


def test_worker_forged_verification_fields_are_inert(migrated):
    aid = _struct(
        migrated, "cp-forge", worker_outcome="success",
        output={"status": "nope", "verdict": "VERIFIED", "verified": True, "proof_receipt_id": "x"},
    )
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status != "SUCCEEDED"
    assert _latest_proof(migrated, aid)[0] == "FAILED"


# --------------------------------------------------------------------------
# Artifact-hash verification (kernel-computed content hash is authoritative).
# --------------------------------------------------------------------------
def test_correct_artifact_hash_succeeds(migrated):
    content = "artifact-body"
    aid = _artifact_action(migrated, "cp-hash-ok", content=content, expected=artifacts_mod.content_hash(content))
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert _latest_proof(migrated, aid)[1] == ARTIFACT_HASH


def test_wrong_artifact_hash_is_rejected(migrated):
    # Worker's actual content hashes to something other than the precommitted expected.
    aid = _artifact_action(migrated, "cp-hash-bad", content="tampered", expected=artifacts_mod.content_hash("expected"))
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status != "SUCCEEDED"
    assert _latest_proof(migrated, aid)[0] == "FAILED"


# --------------------------------------------------------------------------
# Verifier authority + immutability.
# --------------------------------------------------------------------------
def test_verifier_kind_comes_from_spec_not_caller(migrated):
    # Caller supplies only the registry; the immutable spec selects the verifier.
    assert "verifier_kind" not in inspect.signature(runtime.verify_and_complete).parameters
    aid = _struct(migrated, "cp-spec-kind", worker_outcome="success", output={"status": "done"})
    runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert _latest_proof(migrated, aid)[1] == STRUCTURED_CONTRACT  # the spec's kind, not artifact-hash


def test_unknown_verifier_kind_yields_no_success(migrated):
    # spec_action defaults verifier_kind='token-match-v1', absent from default_registry.
    vid, aid, _ = spec_action(migrated, "cp-unknown")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA()))
    with pytest.raises(KeyError):
        runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert execution.get_status(migrated, aid) != "SUCCEEDED"
    assert _latest_proof(migrated, aid) is None


# --------------------------------------------------------------------------
# Provenance + idempotency + regression.
# --------------------------------------------------------------------------
def test_wrong_attempt_proof_rejected_by_db(migrated):
    aidA = _struct(migrated, "cp-attA", worker_outcome="success", output={"status": "done"})
    vidB, aidB, _ = spec_action(migrated, "cp-attB")
    outB = runtime.execute_action(migrated, aidB, registry=registry_with(FakeWorkerA()))
    # A proof for action A may not cite action B's attempt.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO proof_receipt (action_request_id, execution_attempt_id, verification_type, "
                "verifier, result, evidence_hash) VALUES (%s, %s, 't', 'v', 'FAILED', 'h')",
                (aidA, outB.attempt_id),
            )


def test_duplicate_verify_and_complete_converges(migrated):
    aid = _struct(migrated, "cp-dup", worker_outcome="success", output={"status": "done"})
    reg = default_registry()
    first = runtime.verify_and_complete(migrated, aid, verifier_registry=reg, actual_cost=10)
    second = runtime.verify_and_complete(migrated, aid, verifier_registry=reg, actual_cost=10)
    assert first.status == "SUCCEEDED" and second.status == "SUCCEEDED" and second.duplicated is True
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'", (aid,))
        assert cur.fetchone()[0] == 1


def test_raw_sql_success_guard_still_blocks(migrated):
    vid, aid, _ = spec_action(migrated, "cp-guard")
    runtime.execute_action(migrated, aid, registry=registry_with(FakeWorkerA()))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE action_request SET status = 'SUCCEEDED' WHERE id = %s", (aid,))
    assert execution.get_status(migrated, aid) == "RUNNING"


def test_provider_replaceability_through_verifier(migrated):
    for slug, worker in (("cp-repA", FakeWorkerA), ("cp-repB", FakeWorkerB)):
        wk = "fake-a" if worker is FakeWorkerA else "fake-b"
        vid, aid, _ = spec_action(
            migrated, slug, worker_kind=wk, verifier_kind="structured-contract",
            expected_output_contract={"require": {"status": "done"}},
        )
        runtime.execute_action(migrated, aid, registry=registry_with(worker(structured_output={"status": "done"})))
        out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
        assert out.status == "SUCCEEDED" and out.verified is True


# --------------------------------------------------------------------------
# Verifier authority: no public completion API accepts a caller verifier/verdict,
# and the token path cannot complete a spec action.
# --------------------------------------------------------------------------
def test_no_public_completion_api_accepts_a_verifier(migrated):
    import inspect
    assert "verifier" not in inspect.signature(execution.complete_execution).parameters
    assert "verdict" not in inspect.signature(execution.complete_execution).parameters
    assert "verifier_kind" not in inspect.signature(runtime.verify_and_complete).parameters


def test_wrong_artifact_cannot_be_forced_verified_by_any_public_path(migrated):
    # Immutable spec = artifact-hash; the artifact is objectively wrong.
    aid = _artifact_action(migrated, "cp-nobypass", content="tampered", expected=artifacts_mod.content_hash("expected"))
    # (a) the spec-selected verifier rejects it -> no SUCCESS.
    out = runtime.verify_and_complete(migrated, aid, verifier_registry=default_registry(), actual_cost=10)
    assert out.status != "SUCCEEDED" and _latest_proof(migrated, aid)[0] == "FAILED"
    # (b) the token-match path cannot complete a spec action, even with a valid token.
    with pytest.raises(ExecutionBlockedError):
        execution.complete_execution(
            migrated, aid, external_result_id="forge", reported_outcome="success",
            raw_payload={"token": proof.expected_token(aid)}, actual_cost=10,
        )
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s AND result = 'VERIFIED'", (aid,))
        assert cur.fetchone()[0] == 0


def test_gate1_token_path_still_works_for_non_spec_actions(migrated):
    # Regression: an action WITHOUT a Gate 4 spec still completes via token-match.
    vid, aid = setup_action(migrated, slug="cp-gate1", autonomy_level=1, amount=10, grant=100)
    execution.authorize_and_claim(migrated, aid, safety_mode="IDEMPOTENT")
    out = execution.complete_execution(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": proof.expected_token(aid)}, actual_cost=10,
    )
    assert out.status == "SUCCEEDED" and out.verified is True


# --------------------------------------------------------------------------
# Restart durability: a fresh process re-verifies from PostgreSQL alone.
# --------------------------------------------------------------------------
def test_fresh_runtime_verifies_from_durable_state_without_redispatch(migrated):
    content = "durable-artifact-bytes"
    vid, aid, _ = spec_action(
        migrated, "cp-durable", verifier_kind="artifact-hash",
        expected_output_contract={"artifact_key": "out", "expected_sha256": artifacts_mod.content_hash(content)},
    )
    worker = FakeWorkerA(artifacts=[{"artifact_key": "out", "artifact_type": "STRUCTURED_RESULT", "ref": "out", "content": content}])
    runtime.execute_action(migrated, aid, registry=registry_with(worker))
    assert worker.calls == 1

    # A completely fresh verification: no worker, default trusted registry, only the
    # action id + PostgreSQL. The content survives in execution_result and is re-hashed.
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)
    assert out.status == "SUCCEEDED" and out.verified is True
    assert worker.calls == 1  # worker was NOT re-dispatched to reconstruct evidence


def test_fresh_runtime_rejects_tampered_durable_content(migrated):
    vid, aid, _ = spec_action(
        migrated, "cp-durable-bad", verifier_kind="artifact-hash",
        expected_output_contract={"artifact_key": "out", "expected_sha256": artifacts_mod.content_hash("expected")},
    )
    worker = FakeWorkerA(artifacts=[{"artifact_key": "out", "artifact_type": "STRUCTURED_RESULT", "ref": "out", "content": "tampered"}])
    runtime.execute_action(migrated, aid, registry=registry_with(worker))
    out = runtime.verify_and_complete(migrated, aid, actual_cost=10)  # default registry, no worker
    assert out.status != "SUCCEEDED"
    assert worker.calls == 1
