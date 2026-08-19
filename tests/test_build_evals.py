"""Gate 5 / Slice 4 — development builder-quality evals.

Each eval drives the ENTIRE Gate 5 production chain (BUILD authority -> build_spec ->
venture_repository -> execution_spec -> WorkerAdapter -> attempt/result -> substrate +
build_manifest capture -> Technical evidence -> Product/Experience/Commercial/
AntiGeneric -> overall) and asserts verdicts read back from CANONICAL state. The
worker DECLARES candidate structure; the kernel derives verdicts. No expected-outcome
flag is ever handed to production — differences are observable candidate facts only.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from aidan_core.build import manifest as manifest_mod
from aidan_core.build import quality as quality_mod
from aidan_core.build import repository as repo_mod
from aidan_core.build import runtime as build_runtime
from aidan_core.build import substrate as substrate_mod
from aidan_core.build import technical as technical_mod
from aidan_core.build.spec import get_build_spec as build_spec_mod_get
from aidan_core.factory import runtime as factory_runtime
from aidan_core.factory import spec as spec_mod
from aidan_core.errors import BuildAuthorityError, IdempotencyConflictError

from build_fakes import (
    GOOD_CANDIDATE,
    GOOD_PRODUCT_MANIFEST,
    BuilderWorker,
    ScriptedBuilderWorker,
    build_authority,
    freeze_default_build_spec,
    full_eval,
    make_substrate,
    registry_with,
)

_CAPS = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]

# A build_spec whose venture genuinely requires a conversational workflow (case D).
_CHAT_SPEC = dict(
    primary_workflow="triage chat -> risk assess -> escalate",
    required_capabilities=["triage_chat"], differentiators=["clinical triage model"],
    excluded_capabilities=["generic_crm"])
_CHAT_PM = dict(GOOD_PRODUCT_MANIFEST, workflows=["triage chat -> risk assess -> escalate"],
                features=["triage_chat"], differentiators_implemented=["clinical triage model"],
                vocabulary=["triage", "clinic", "risk"])


def _pm(**over):
    d = dict(GOOD_PRODUCT_MANIFEST)
    d.update(over)
    return d


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


# ---- A–R: dimension outcomes through the full chain ---------------------------------
def test_A_venture_specific_all_pass(migrated):
    r = full_eval(migrated, "evA", product_manifest=GOOD_PRODUCT_MANIFEST)
    assert r.technical == "PASS" and r.dimensions == {
        "PRODUCT": "PASS", "EXPERIENCE": "PASS", "COMMERCIAL": "PASS", "ANTIGENERIC": "PASS"}
    assert r.overall == "PASS"


def test_B_generic_dashboard_antigeneric_fail(migrated):
    r = full_eval(migrated, "evB", product_manifest=_pm(
        workflows=["generic admin dashboard"], features=["dashboard", "kpi_cards", "crud", "settings"],
        differentiators_implemented=[], vocabulary=["dashboard"]))
    assert r.technical == "PASS"
    assert r.dimensions["PRODUCT"] == "FAIL" and r.dimensions["ANTIGENERIC"] == "FAIL"
    assert r.overall == "FAIL"


def test_C_generic_ai_chat_fail(migrated):
    r = full_eval(migrated, "evC", product_manifest=_pm(
        workflows=["ai chat"], features=["ai_chat"], differentiators_implemented=[], vocabulary=["assistant"]))
    assert r.dimensions["PRODUCT"] == "FAIL" and r.dimensions["ANTIGENERIC"] == "FAIL"


def test_D_legitimate_conversational_product_not_generic(migrated):
    r = full_eval(migrated, "evD", spec_overrides=_CHAT_SPEC, product_manifest=_CHAT_PM)
    assert r.dimensions["ANTIGENERIC"] == "PASS" and r.dimensions["PRODUCT"] == "PASS"
    assert r.overall == "PASS"


def test_E_fake_feature_claimed_not_in_structure(migrated):
    # worker asserts the feature via prose but the candidate structure omits it
    r = full_eval(migrated, "evE", product_manifest=_pm(features=["approval_tracking"]),
                  worker_claims={"product_pass": True, "claimed_feature": "preauth_drafting"})
    assert r.dimensions["PRODUCT"] == "FAIL" and r.overall == "FAIL"


def test_F_missing_differentiator_fail(migrated):
    r = full_eval(migrated, "evF", product_manifest=_pm(differentiators_implemented=[]))
    assert r.dimensions["PRODUCT"] == "FAIL" and r.dimensions["ANTIGENERIC"] == "FAIL"


def test_G_wrong_buyer_product_and_commercial_fail(migrated):
    r = full_eval(migrated, "evG", product_manifest=_pm(
        buyer="corporate legal departments", workflows=["contract intake -> review -> approve"],
        features=["contract_review"], differentiators_implemented=[]))
    assert r.dimensions["PRODUCT"] == "FAIL" and r.dimensions["COMMERCIAL"] == "FAIL"


def test_H_missing_required_capability_fail(migrated):
    r = full_eval(migrated, "evH", product_manifest=_pm(features=["preauth_drafting"]))
    assert r.dimensions["PRODUCT"] == "FAIL" and r.overall == "FAIL"


def test_I_excluded_capability_present_fail(migrated):
    r = full_eval(migrated, "evI", product_manifest=_pm(
        features=["preauth_drafting", "approval_tracking", "generic_crm"]))
    assert r.dimensions["PRODUCT"] == "FAIL"


def test_J_experience_dead_end_only(migrated):
    r = full_eval(migrated, "evJ", product_manifest=_pm(dead_ends=["draft pre-auth"]))
    assert r.dimensions["EXPERIENCE"] == "FAIL"
    assert r.dimensions["PRODUCT"] == "PASS" and r.dimensions["COMMERCIAL"] == "PASS"
    assert r.overall == "FAIL"


def test_K_missing_required_state_experience_fail(migrated):
    r = full_eval(migrated, "evK", contract_extra={"experience": {"required_states": ["error"]}},
                  product_manifest=_pm(states=["empty"]))
    assert r.dimensions["EXPERIENCE"] == "FAIL"


def test_L_commercial_mechanism_aligned_pass(migrated):
    r = full_eval(migrated, "evL", contract_extra={"commercial": {"requires_conversion": True}},
                  product_manifest=GOOD_PRODUCT_MANIFEST)
    assert r.dimensions["COMMERCIAL"] == "PASS"


def test_M_commercial_contradiction_fail(migrated):
    r = full_eval(migrated, "evM", product_manifest=_pm(
        features=["preauth_drafting", "approval_tracking", "generic_chatbot"]))
    assert r.dimensions["COMMERCIAL"] == "FAIL"


def test_N_missing_conversion_path_commercial_fail(migrated):
    r = full_eval(migrated, "evN", contract_extra={"commercial": {"requires_conversion": True}},
                  product_manifest=_pm(cta=[]))
    assert r.dimensions["COMMERCIAL"] == "FAIL"


def test_O_technical_failure_only_blocks_overall(migrated):
    # forbidden secret file -> Technical FAIL while product dimensions pass
    r = full_eval(migrated, "evO", candidate_files=GOOD_CANDIDATE + [{"path": ".env", "content": "SECRET=x"}],
                  product_manifest=GOOD_PRODUCT_MANIFEST)
    assert r.technical == "FAIL"
    assert all(v == "PASS" for v in r.dimensions.values())
    assert r.overall == "FAIL"


def test_P_worker_claims_all_pass_inert(migrated):
    r = full_eval(migrated, "evP", product_manifest=_pm(features=["approval_tracking"]),
                  worker_claims={"technical_pass": True, "product_pass": True, "experience_pass": True,
                                 "commercial_pass": True, "antigeneric_pass": True, "overall_pass": True})
    assert r.dimensions["PRODUCT"] == "FAIL" and r.overall == "FAIL"


def test_Q_reviewer_claims_pass_inert(migrated):
    reviews = [{"dimension": "PRODUCT", "criterion_id": "polish", "observation": "looks great",
                "interpretation": "PASS", "verdict": "PASS", "source_refs": ["s1"]}]
    r = full_eval(migrated, "evQ", product_manifest=_pm(features=["approval_tracking"]),
                  reviewer_observations=reviews)
    assert r.dimensions["PRODUCT"] == "FAIL"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM build_quality_evidence WHERE build_manifest_id = %s "
                    "AND source_type = 'REVIEW_OBSERVATION'", (r.manifest_id,))
        assert cur.fetchone()[0] == 1


def test_R_execution_succeeded_but_technical_fail(migrated):
    # candidate omits the required file -> Technical FAIL; Gate 4 execution still SUCCEEDS
    r = full_eval(migrated, "evR", candidate_files=[{"path": "readme.txt", "content": "x"}],
                  product_manifest=GOOD_PRODUCT_MANIFEST)
    outc = factory_runtime.verify_and_complete(migrated, r.auth.action_id, actual_cost=0)
    assert outc.status == "SUCCEEDED"
    assert r.technical == "FAIL" and r.overall == "FAIL"


# ---- S–Z: authority / isolation / integrity through the full chain ------------------
def test_S_generic_factory_build_bypass_rejected(migrated):
    auth = build_authority(migrated, slug="evS")
    with pytest.raises(BuildAuthorityError):  # BUILD action, no build_spec bound
        spec_mod.create_execution_spec(
            migrated, auth.action_id, worker_kind="builder-a", verifier_kind="structured-contract",
            timeout_seconds=60, max_attempts=1, capability_scope=["READ_REPOSITORY"],
            task_payload={"free_form": "make me an app"})


def test_T_cross_venture_repository_rejected(migrated):
    # a build for venture A can never bind venture B's repository into its execution_spec
    a = build_authority(migrated, slug="evTa", key="Ta")
    freeze_default_build_spec(migrated, a)
    b = build_authority(migrated, slug="evTb", key="Tb")
    repo_b = repo_mod.register_venture_repository(migrated, b.venture_id, repository_ref="venture://evTb/app")
    bs = build_spec_mod_get(migrated, a.action_id)
    with pytest.raises(BuildAuthorityError):
        spec_mod.create_execution_spec(
            migrated, a.action_id, worker_kind="builder-a", verifier_kind="structured-contract",
            timeout_seconds=60, max_attempts=1, capability_scope=["READ_REPOSITORY"],
            task_payload={"build": {"build_spec_id": str(bs[0]), "build_spec_hash": bs[16],
                                    "venture_repository_id": str(repo_b.venture_repository_id)}})


def test_U_cross_venture_quality_evidence_rejected(migrated):
    a = full_eval(migrated, "evUa", key="Ua", product_manifest=GOOD_PRODUCT_MANIFEST)
    b = full_eval(migrated, "evUb", key="Ub", product_manifest=GOOD_PRODUCT_MANIFEST)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO build_quality_evidence (venture_id, build_manifest_id, dimension, criterion, "
                "source_type, evidence_payload, evidence_hash) VALUES (%s, %s, 'PRODUCT', 'X', 'KERNEL_DERIVED', '{}', 'h')",
                (b.auth.venture_id, a.manifest_id))


def test_V_cross_attempt_manifest_rejected(migrated):
    a = full_eval(migrated, "evVa", key="Va", product_manifest=GOOD_PRODUCT_MANIFEST)
    b = full_eval(migrated, "evVb", key="Vb", product_manifest=GOOD_PRODUCT_MANIFEST)
    with migrated.cursor() as cur:
        cur.execute("SELECT execution_attempt_id FROM build_manifest WHERE id = %s", (b.manifest_id,))
        b_attempt = cur.fetchone()[0]
    # venture A's action + venture B's attempt -> composite FK (attempt,action) has no row
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        manifest_mod.capture_build_manifest(
            migrated, a.auth.action_id, execution_attempt_id=b_attempt,
            substrate_release_id=a.rel.substrate_release_id, candidate_files=GOOD_CANDIDATE,
            workspace_root=_fresh_ws())


def _fresh_ws():
    import tempfile
    return tempfile.mkdtemp(prefix="ev-ws-")


def test_W_attempt1_fail_attempt2_pass_history_preserved(migrated):
    rel = make_substrate(migrated, key="rel-evW")
    auth = build_authority(migrated, slug="evW", key="W")
    freeze_default_build_spec(migrated, auth, expected_output_contract={
        "require": {"status": "done"},
        "technical": {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}})
    repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="venture://evW/app")
    worker = ScriptedBuilderWorker([
        {"status": "wrong", "candidate_files": GOOD_CANDIDATE, "product_manifest": _pm(differentiators_implemented=[])},
        {"status": "done", "candidate_files": GOOD_CANDIDATE, "product_manifest": GOOD_PRODUCT_MANIFEST}])
    reg = registry_with(worker)
    common = dict(worker_kind="builder-a", verifier_kind="structured-contract", capability_scope=_CAPS,
                  timeout_seconds=60, max_attempts=2)
    _, r1 = build_runtime.execute_build(migrated, auth.action_id, registry=reg, **common)
    cap1 = build_runtime.capture_and_check_build(migrated, auth.action_id, substrate_release_id=rel.substrate_release_id,
                                                 execution_attempt_id=r1.attempt_id)
    build_runtime.assess_build_quality(migrated, auth.action_id, execution_attempt_id=r1.attempt_id)
    factory_runtime.verify_and_complete(migrated, auth.action_id, actual_cost=0)
    _, r2 = build_runtime.execute_build(migrated, auth.action_id, registry=reg, **common)
    cap2 = build_runtime.capture_and_check_build(migrated, auth.action_id, substrate_release_id=rel.substrate_release_id,
                                                 execution_attempt_id=r2.attempt_id)
    build_runtime.assess_build_quality(migrated, auth.action_id, execution_attempt_id=r2.attempt_id)
    assert r1.attempt_id != r2.attempt_id
    assert quality_mod.dimension_verdict(migrated, cap1["manifest"].build_manifest_id, "PRODUCT") == "FAIL"
    assert quality_mod.overall_verdict(migrated, cap2["manifest"].build_manifest_id) == "PASS"


def test_X_changed_intent_requires_new_build_spec(migrated):
    auth = build_authority(migrated, slug="evX")
    freeze_default_build_spec(migrated, auth)
    with pytest.raises(IdempotencyConflictError):  # same action, materially changed intent
        freeze_default_build_spec(migrated, auth, primary_workflow="a completely different journey")


def test_Y_substrate_provenance_reconstructable(migrated):
    r = full_eval(migrated, "evY", product_manifest=GOOD_PRODUCT_MANIFEST)
    with migrated.cursor() as cur:
        cur.execute("SELECT substrate_release_id, file_manifest FROM build_manifest WHERE id = %s", (r.manifest_id,))
        rel_id, file_manifest = cur.fetchone()
    rel = substrate_mod.get_substrate_release(migrated, rel_id)
    assert rel[3] == "sha-0016"  # exact source sha
    comp_paths = {c["path"] for c in rel[5]}
    manifest_paths = {e["path"] for e in file_manifest}
    assert comp_paths <= manifest_paths  # every substrate component file present in the candidate tree


def test_Z_substrate_is_infrastructure_only(migrated):
    root = substrate_mod.default_substrate_root()
    substrate_mod.assert_substrate_scope(root, ["CONFIG_BOUNDARY", "TEST_HARNESS"])  # no product-template material


# ---- consolidated Technical integrity (actual workspace mutations) ------------------
def test_technical_tamper_detected(migrated):
    # dispatch-then-manually-capture so technical checks run for the FIRST time on the
    # tampered workspace (evidence is immutable-first, so a re-run would return PASS).
    from build_fakes import dispatched_build
    rel = make_substrate(migrated, key="rel-evTamper")
    auth, _worker, _d, result = dispatched_build(migrated, "evTamper")
    root = _fresh_ws()
    m = manifest_mod.capture_build_manifest(
        migrated, auth.action_id, execution_attempt_id=result.attempt_id,
        substrate_release_id=rel.substrate_release_id, candidate_files=GOOD_CANDIDATE, workspace_root=root)
    with open(os.path.join(root, "app", "main.py"), "wb") as f:
        f.write(b"TAMPERED")
    res = technical_mod.run_technical_checks(migrated, m.build_manifest_id, root)
    assert {c["name"]: c["result"] for c in res["checks"]}["MANIFEST_INTEGRITY"] == "FAIL"


def _rel_of(conn, manifest_id):
    with conn.cursor() as cur:
        cur.execute("SELECT substrate_release_id FROM build_manifest WHERE id = %s", (manifest_id,))
        return cur.fetchone()[0]


def _latest_attempt(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT execution_attempt_id FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC, id DESC LIMIT 1", (aid,))
        return cur.fetchone()[0]


def test_worker_fake_hash_ignored(migrated):
    r = full_eval(migrated, "evHash", candidate_files=[{"path": "app/main.py", "content": "REAL", "sha256": "bogus"}],
                  product_manifest=GOOD_PRODUCT_MANIFEST)
    with migrated.cursor() as cur:
        cur.execute("SELECT file_manifest FROM build_manifest WHERE id = %s", (r.manifest_id,))
        fm = cur.fetchone()[0]
    from aidan_core.build import workspace as ws
    entry = next(e for e in fm if e["path"] == "app/main.py")
    assert entry["sha256"] == ws.hash_bytes(b"REAL") and entry["sha256"] != "bogus"


def test_path_escape_rejected_before_capture(migrated):
    from build_fakes import captured_manifest
    auth, mid = captured_manifest(migrated, "evEsc")
    with pytest.raises(BuildAuthorityError):
        manifest_mod.capture_build_manifest(
            migrated, auth.action_id, execution_attempt_id=_latest_attempt(migrated, auth.action_id),
            substrate_release_id=_rel_of(migrated, mid),
            candidate_files=[{"path": "../escape.py", "content": "x"}], workspace_root=_fresh_ws())


def test_no_lifecycle_or_deploy_on_quality_pass(migrated):
    r = full_eval(migrated, "evLC", product_manifest=GOOD_PRODUCT_MANIFEST)
    assert r.overall == "PASS"
    assert _lifecycle(migrated, r.auth.venture_id) == "DISCOVERED"
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.deployment')")
        assert cur.fetchone()[0] is None
