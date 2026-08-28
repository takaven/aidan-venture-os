"""Gate 5 / Slice 2 — Venture Substrate + build manifest + deterministic Technical quality.

Proves: an immutable infrastructure substrate release with exact source provenance;
a disposable root-contained venture workspace; a kernel-hashed, attempt-bound,
immutable build_manifest; and deterministic Technical evidence whose PASS/FAIL is
derived (no scalar) from machine checks — never from worker/model self-report, and
independent of the Gate 4 execution status. No Product/Experience/Commercial/
AntiGeneric evaluation, no deployment, no lifecycle change.
"""
from __future__ import annotations

import os
import tempfile

import psycopg
import pytest

from aidan_core.build import manifest as manifest_mod
from aidan_core.build import repository as repo_mod
from aidan_core.build import runtime as build_runtime
from aidan_core.build import substrate as substrate_mod
from aidan_core.build import technical as technical_mod
from aidan_core.build import workspace as ws
from aidan_core.errors import BuildAuthorityError, IdempotencyConflictError
from aidan_core.factory import runtime as factory_runtime

from build_fakes import (
    GOOD_CANDIDATE,
    BuilderWorker,
    ScriptedBuilderWorker,
    build_authority,
    dispatched_build,
    freeze_default_build_spec,
    make_substrate,
    registry_with,
)


def _ws(tmp_path, name="w"):
    p = tmp_path / name
    p.mkdir()
    return str(p)


def _proof_count(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s", (aid,))
        return cur.fetchone()[0]


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


# ==========================================================================
# Substrate release identity (matrix 1-8)
# ==========================================================================
def test_1_release_created_with_source_sha(migrated):
    res = make_substrate(migrated, key="r1", sha="os-sha-abc")
    assert res.created is True
    row = substrate_mod.get_substrate_release(migrated, res.substrate_release_id)
    assert row[3] == "os-sha-abc" and sorted(row[4]) == ["CONFIG_BOUNDARY", "TEST_HARNESS"]


def test_2_exact_replay_converges(migrated):
    a = make_substrate(migrated, key="r2", sha="s")
    b = make_substrate(migrated, key="r2", sha="s")
    assert b.created is False and b.substrate_release_id == a.substrate_release_id


def test_3_changed_source_sha_conflicts(migrated):
    make_substrate(migrated, key="r3", sha="s-one")
    with pytest.raises(IdempotencyConflictError):
        make_substrate(migrated, key="r3", sha="s-two")


def test_4_changed_component_selection_conflicts(migrated):
    make_substrate(migrated, key="r4", sha="s", components=["CONFIG_BOUNDARY", "TEST_HARNESS"])
    with pytest.raises(IdempotencyConflictError):
        make_substrate(migrated, key="r4", sha="s", components=["CONFIG_BOUNDARY"])


def test_5_unknown_component_rejected(migrated):
    with pytest.raises(ValueError):
        make_substrate(migrated, key="r5", sha="s", components=["CONFIG_BOUNDARY", "PRICING_PAGE"])


def test_6_component_order_hash_insensitive():
    root = substrate_mod.default_substrate_root()
    cm = substrate_mod._build_component_manifest(root, ["CONFIG_BOUNDARY", "TEST_HARNESS"])
    h1 = substrate_mod.compute_release_hash(
        source_repository_ref="r", source_sha="s", components=["CONFIG_BOUNDARY", "TEST_HARNESS"], component_manifest=cm)
    h2 = substrate_mod.compute_release_hash(
        source_repository_ref="r", source_sha="s", components=["TEST_HARNESS", "CONFIG_BOUNDARY"], component_manifest=cm)
    assert h1 == h2


def test_7_substrate_contains_no_product_template(tmp_path):
    root = substrate_mod.default_substrate_root()
    substrate_mod.assert_substrate_scope(root, ["CONFIG_BOUNDARY", "TEST_HARNESS"])  # real substrate is clean
    # a substrate source containing product-template material is rejected
    fake = tmp_path / "fake_substrate" / "config_boundary"
    fake.mkdir(parents=True)
    (fake / "dashboard.json").write_text("{}")
    with pytest.raises(BuildAuthorityError):
        substrate_mod.assert_substrate_scope(tmp_path / "fake_substrate", ["CONFIG_BOUNDARY"])


def test_8_materializes_only_allowed_paths(tmp_path):
    root = substrate_mod.default_substrate_root()
    release_row = (None, None, None, None, ["CONFIG_BOUNDARY", "TEST_HARNESS"], [], None, None)
    entries = substrate_mod.materialize_substrate(release_row, _ws(tmp_path), source_root=root)
    paths = {e["path"] for e in entries}
    assert paths == {
        "config_boundary/config.contract.json", "config_boundary/README.md",
        "test_harness/test.contract.json", "test_harness/README.md",
    }
    assert all(e["origin"] == "substrate" for e in entries)


# ==========================================================================
# Workspace containment (matrix 9-12) — pure filesystem logic
# ==========================================================================
def test_9_root_containment(tmp_path):
    root = _ws(tmp_path)
    meta = ws.write_candidate(root, "app/main.py", b"x")
    assert meta["path"] == "app/main.py" and os.path.exists(os.path.join(root, "app", "main.py"))


@pytest.mark.parametrize("bad", ["../escape", "../../x", "a/../../b", "sub/../../../out"])
def test_10_dotdot_escape_rejected(tmp_path, bad):
    with pytest.raises(BuildAuthorityError):
        ws.contained_path(_ws(tmp_path), bad)


@pytest.mark.parametrize("bad", ["/abs/x", "C:/x", "\\\\server\\share"])
def test_11_absolute_path_rejected(tmp_path, bad):
    with pytest.raises(BuildAuthorityError):
        ws.contained_path(_ws(tmp_path), bad)


def test_12_symlink_escape_rejected(tmp_path):
    root = _ws(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = os.path.join(root, "link")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    with pytest.raises(BuildAuthorityError):
        ws.write_candidate(root, "link/escaped.py", b"x")


def test_os_workspace_rejected():
    # Use the trusted, installation-independent canonical identity (not the substrate
    # location, which now lives inside the package as a resource).
    os_root = ws.canonical_os_repo_root()
    assert os_root is not None  # from a source checkout the fallback identifies the repo
    with pytest.raises(BuildAuthorityError):
        ws.assert_isolated_workspace(str(os_root))


# ==========================================================================
# Candidate capture + build_manifest (matrix 13-23)
# ==========================================================================
def test_13_14_builder_ab_candidate_captured(migrated, tmp_path):
    rel = make_substrate(migrated, key="c13")
    for kind, slug, k in (("builder-a", "c13a", "A"), ("builder-b", "c13b", "B")):
        worker = BuilderWorker(structured_output={"status": "done", "candidate_files": GOOD_CANDIDATE})
        worker.kind = kind
        auth, w, _, _ = dispatched_build(migrated, slug, key=k, worker=worker)
        out = build_runtime.capture_and_check_build(
            migrated, auth.action_id, substrate_release_id=rel.substrate_release_id,
            workspace_root=_ws(tmp_path, slug))
        paths = {e["path"] for e in out["manifest"].file_manifest}
        assert "app/main.py" in paths


def test_15_16_kernel_derived_hashes_ignore_worker_hash(migrated, tmp_path):
    rel = make_substrate(migrated, key="c15")
    # worker declares a bogus sha256 for its file; the kernel must ignore it.
    candidate = [{"path": "app/main.py", "content": "REAL", "sha256": "deadbeefbogus"}]
    auth, _, _, _ = dispatched_build(migrated, "c15", candidate_files=candidate)
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    entry = next(e for e in out["manifest"].file_manifest if e["path"] == "app/main.py")
    assert entry["sha256"] == ws.hash_bytes(b"REAL") and entry["sha256"] != "deadbeefbogus"


def test_17_manifest_exact_replay_converges(migrated, tmp_path):
    rel = make_substrate(migrated, key="c17")
    auth, _, _, _ = dispatched_build(migrated, "c17")
    kw = dict(substrate_release_id=rel.substrate_release_id)
    first = build_runtime.capture_and_check_build(migrated, auth.action_id, workspace_root=_ws(tmp_path, "a"), **kw)
    again = build_runtime.capture_and_check_build(migrated, auth.action_id, workspace_root=_ws(tmp_path, "b"), **kw)
    assert again["manifest"].created is False
    assert again["manifest"].build_manifest_id == first["manifest"].build_manifest_id


def test_18_changed_manifest_conflicts(migrated, tmp_path):
    # Same attempt, but a tampered candidate set -> hard conflict (history not overwritten).
    rel = make_substrate(migrated, key="c18")
    auth, _, _, result = dispatched_build(migrated, "c18")
    build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path, "a"))
    attempt_id = manifest_mod.get_build_manifest(migrated, result.attempt_id)[3]
    with pytest.raises(IdempotencyConflictError):
        manifest_mod.capture_build_manifest(
            migrated, auth.action_id, execution_attempt_id=attempt_id,
            substrate_release_id=rel.substrate_release_id,
            candidate_files=[{"path": "app/main.py", "content": "TAMPERED-DIFFERENT"}],
            workspace_root=_ws(tmp_path, "b"))


def test_19_manifest_update_rejected(migrated, tmp_path):
    rel = make_substrate(migrated, key="c19")
    auth, _, _, _ = dispatched_build(migrated, "c19")
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE build_manifest SET manifest_hash = 'x' WHERE id = %s",
                        (out["manifest"].build_manifest_id,))


def test_20_manifest_delete_rejected(migrated, tmp_path):
    rel = make_substrate(migrated, key="c20")
    auth, _, _, _ = dispatched_build(migrated, "c20")
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM build_manifest WHERE id = %s", (out["manifest"].build_manifest_id,))


def test_21_22_cross_attempt_binding_rejected(migrated, tmp_path):
    # A manifest cannot be attached to an execution attempt from another action/venture.
    rel = make_substrate(migrated, key="c21")
    a, _, _, _ = dispatched_build(migrated, "c21a", key="A")
    b, _, _, b_result = dispatched_build(migrated, "c21b", key="B")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        manifest_mod.capture_build_manifest(
            migrated, a.action_id, execution_attempt_id=b_result.attempt_id,
            substrate_release_id=rel.substrate_release_id, candidate_files=GOOD_CANDIDATE,
            workspace_root=_ws(tmp_path))


def test_23_provenance_chain_preserved(migrated, tmp_path):
    rel = make_substrate(migrated, key="c23")
    auth, _, _, result = dispatched_build(migrated, "c23")
    build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    row = manifest_mod.get_build_manifest(migrated, result.attempt_id)
    # venture, action, build_spec, repo, substrate all consistent
    bs = repo_mod  # noqa: F841
    assert str(row[2]) == str(auth.action_id)
    assert str(row[6]) == str(rel.substrate_release_id)
    with migrated.cursor() as cur:
        cur.execute("SELECT id FROM build_spec WHERE action_request_id = %s", (auth.action_id,))
        assert str(row[4]) == str(cur.fetchone()[0])


# ==========================================================================
# Deterministic Technical quality (matrix 24-29, 31)
# ==========================================================================
def test_24_missing_required_file_fail(migrated, tmp_path):
    rel = make_substrate(migrated, key="t24")
    auth, _, _, _ = dispatched_build(migrated, "t24", candidate_files=[{"path": "readme.txt", "content": "x"}])
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    assert out["verdict"] == "FAIL"
    names = {c["name"]: c["result"] for c in out["checks"]}
    assert names["REQUIRED_OUTPUT"] == "FAIL"


def test_25_required_file_present_pass(migrated, tmp_path):
    rel = make_substrate(migrated, key="t25")
    auth, _, _, _ = dispatched_build(migrated, "t25")
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    assert out["verdict"] == "PASS"
    assert all(c["result"] == "PASS" for c in out["checks"])


def test_26_forbidden_secret_file_fail(migrated, tmp_path):
    rel = make_substrate(migrated, key="t26")
    candidate = GOOD_CANDIDATE + [{"path": ".env", "content": "SECRET=abc"}]
    auth, _, _, _ = dispatched_build(migrated, "t26", candidate_files=candidate)
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    names = {c["name"]: c["result"] for c in out["checks"]}
    assert names["FORBIDDEN_SECRET_FILE"] == "FAIL" and out["verdict"] == "FAIL"


def test_27_worker_claims_technical_pass_inert(migrated, tmp_path):
    rel = make_substrate(migrated, key="t27")
    # worker asserts everything passed, but omits the required file
    so = {"status": "done", "tests_passed": True, "quality_pass": True, "technical_pass": True,
          "candidate_files": [{"path": "readme.txt", "content": "x"}]}
    worker = BuilderWorker(structured_output=so)
    auth, _, _, _ = dispatched_build(migrated, "t27", worker=worker)
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    assert out["verdict"] == "FAIL"


def test_28_execution_succeeded_but_technical_fail(migrated, tmp_path):
    # Gate 4 execution reaches SUCCEEDED, yet the candidate violates the Technical
    # contract -> Technical FAIL. Execution SUCCESS != Technical PASS.
    rel = make_substrate(migrated, key="t28")
    auth, _, _, _ = dispatched_build(migrated, "t28", candidate_files=[{"path": "readme.txt", "content": "x"}])
    outc = factory_runtime.verify_and_complete(migrated, auth.action_id, actual_cost=0)
    assert outc.status == "SUCCEEDED"
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    assert out["verdict"] == "FAIL"


def test_29_valid_candidate_all_checks_pass_and_verdict_derived(migrated, tmp_path):
    rel = make_substrate(migrated, key="t29")
    auth, _, _, _ = dispatched_build(migrated, "t29")
    out = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    assert out["verdict"] == "PASS"
    assert technical_mod.technical_verdict(migrated, out["manifest"].build_manifest_id) == "PASS"


def test_manifest_integrity_detects_tamper(migrated, tmp_path):
    rel = make_substrate(migrated, key="tamp")
    auth, _, _, _ = dispatched_build(migrated, "tamp")
    root = _ws(tmp_path)
    m = manifest_mod.capture_build_manifest(
        migrated, auth.action_id,
        execution_attempt_id=_latest_attempt(migrated, auth.action_id),
        substrate_release_id=rel.substrate_release_id, candidate_files=GOOD_CANDIDATE, workspace_root=root)
    # tamper the actual workspace bytes AFTER capture, BEFORE first check run
    with open(os.path.join(root, "app", "main.py"), "wb") as f:
        f.write(b"TAMPERED-ON-DISK")
    res = technical_mod.run_technical_checks(migrated, m.build_manifest_id, root)
    names = {c["name"]: c["result"] for c in res["checks"]}
    assert names["MANIFEST_INTEGRITY"] == "FAIL" and res["verdict"] == "FAIL"


def test_31_no_aggregate_score_column(migrated):
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'build_technical_check' AND column_name IN ('score', 'quality_score')")
        assert cur.fetchone()[0] == 0


# ==========================================================================
# Retry history + no side effects (matrix 30, 32, 33)
# ==========================================================================
def _latest_attempt(conn, aid):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT execution_attempt_id FROM execution_result WHERE action_request_id = %s "
            "ORDER BY received_at DESC, id DESC LIMIT 1", (aid,))
        return cur.fetchone()[0]


def test_30_attempt1_fail_attempt2_corrected_both_preserved(migrated, tmp_path):
    rel = make_substrate(migrated, key="h30")
    auth = build_authority(migrated, slug="h30", key="H")
    contract = {"require": {"status": "done"},
                "technical": {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}}
    freeze_default_build_spec(migrated, auth, expected_output_contract=contract)
    repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="venture://h30/app")
    worker = ScriptedBuilderWorker([
        {"status": "wrong", "candidate_files": [{"path": "readme.txt", "content": "bad"}]},   # attempt 1
        {"status": "done", "candidate_files": GOOD_CANDIDATE},                                  # attempt 2
    ])
    reg = registry_with(worker)
    common = dict(worker_kind="builder-a", verifier_kind="structured-contract",
                  capability_scope=["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"],
                  timeout_seconds=60, max_attempts=2)
    # attempt 1: capture manifest, then verification rejects -> retryable
    _, r1 = build_runtime.execute_build(migrated, auth.action_id, registry=reg, **common)
    cap1 = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id,
        execution_attempt_id=r1.attempt_id, workspace_root=_ws(tmp_path, "a1"))
    factory_runtime.verify_and_complete(migrated, auth.action_id, actual_cost=0)  # rejects attempt 1
    # attempt 2: corrected candidate
    _, r2 = build_runtime.execute_build(migrated, auth.action_id, registry=reg, **common)
    cap2 = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id,
        execution_attempt_id=r2.attempt_id, workspace_root=_ws(tmp_path, "a2"))
    assert r1.attempt_id != r2.attempt_id
    assert cap1["verdict"] == "FAIL" and cap2["verdict"] == "PASS"
    assert manifest_mod.get_build_manifest(migrated, r1.attempt_id) is not None  # attempt-1 history preserved
    assert manifest_mod.get_build_manifest(migrated, r2.attempt_id) is not None


def test_32_33_no_proof_lifecycle_or_deploy_side_effects(migrated, tmp_path):
    rel = make_substrate(migrated, key="se")
    auth, _, _, _ = dispatched_build(migrated, "se")
    build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, workspace_root=_ws(tmp_path))
    assert _proof_count(migrated, auth.action_id) == 0        # Slice 2 records no proof
    assert _lifecycle(migrated, auth.venture_id) == "DISCOVERED"
    with migrated.cursor() as cur:  # no deployment table leaked
        cur.execute("SELECT to_regclass('public.deployment'), to_regclass('public.quality_assessment')")
        assert cur.fetchone() == (None, None)


# ==========================================================================
# Technical contract schema + command allowlist (pure logic; matrix 22 keys)
# ==========================================================================
def test_unknown_technical_contract_key_fails():
    res = technical_mod._check_contract_schema({"required_files": [], "bogus_key": 1})
    assert res.result == "FAIL" and "bogus_key" in res.detail["unknown_keys"]


def test_test_command_allowlist():
    assert technical_mod._check_test_command({"required_commands": ["pytest"]}).result == "PASS"
    assert technical_mod._check_test_command({"required_commands": ["rm -rf /"]}).result == "FAIL"
    assert technical_mod._check_test_command({}).result == "PASS"
