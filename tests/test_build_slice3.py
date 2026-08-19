"""Gate 5 / Slice 3 — Product / Experience / Commercial / AntiGeneric quality.

Five DISTINCT dimensions (Technical reused from Slice 2 + the four here), no scalar
score, no compensation: overall PASS iff all five PASS. Verdicts are kernel-derived
from evidence; worker and reviewer self-reports are inert; AntiGeneric is judged
relative to the frozen build_spec (not a keyword ban); commercial quality is
implementation alignment, never market outcome. Append-only evidence + assessment;
cross-venture/manifest mixing rejected; retry history preserved.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core.build import quality as quality_mod
from aidan_core.build import repository as repo_mod
from aidan_core.build import runtime as build_runtime
from aidan_core.errors import IdempotencyConflictError
from aidan_core.factory import runtime as factory_runtime

from build_fakes import (
    GOOD_CANDIDATE,
    GOOD_PRODUCT_MANIFEST,
    BuilderWorker,
    ScriptedBuilderWorker,
    build_authority,
    captured_manifest,
    freeze_default_build_spec,
    make_substrate,
    registry_with,
)

_CAPS = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]


def _mf(**overrides):
    d = dict(GOOD_PRODUCT_MANIFEST)
    d.update(overrides)
    return d


def _assess(conn, manifest_id, descriptor, **kw):
    return quality_mod.run_quality_assessment(conn, manifest_id, product_descriptor=descriptor, **kw)


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


# ==========================================================================
# Dimension outcomes (matrix 1-13)
# ==========================================================================
def test_1_valid_candidate_all_five_pass(migrated):
    auth, mid = captured_manifest(migrated, "q1")
    out = _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    assert out["dimensions"] == {"PRODUCT": "PASS", "EXPERIENCE": "PASS",
                                 "COMMERCIAL": "PASS", "ANTIGENERIC": "PASS"}
    assert out["overall"] == "PASS"  # Technical (Slice 2) also PASS


def test_2_generic_dashboard_antigeneric_fail(migrated):
    auth, mid = captured_manifest(migrated, "q2")
    d = _mf(workflows=["generic admin dashboard"], features=["dashboard", "kpi_cards", "crud", "settings"],
            differentiators_implemented=[], vocabulary=["dashboard"])
    out = _assess(migrated, mid, d)
    assert quality_mod.dimension_verdict(migrated, mid, "ANTIGENERIC") == "FAIL"
    assert out["dimensions"]["PRODUCT"] == "FAIL"
    assert out["overall"] == "FAIL"  # Technical PASS cannot compensate


def test_3_ai_chat_not_required_fails(migrated):
    auth, mid = captured_manifest(migrated, "q3")
    d = _mf(workflows=["ai chat"], features=["ai_chat"], differentiators_implemented=[], vocabulary=["assistant"])
    out = _assess(migrated, mid, d)
    assert out["dimensions"]["PRODUCT"] == "FAIL" and out["dimensions"]["ANTIGENERIC"] == "FAIL"


def test_3b_chat_required_by_spec_is_not_generic(migrated):
    # If the venture genuinely requires a conversational workflow, chat is NOT generic.
    auth = build_authority(migrated, slug="q3b", key="q3b")
    rel = make_substrate(migrated, key="rel-q3b")
    freeze_default_build_spec(
        migrated, auth, primary_workflow="ai chat triage -> escalate",
        required_capabilities=["ai_chat"], differentiators=["clinical triage model"],
        excluded_capabilities=[], expected_output_contract={"require": {"status": "done"},
        "technical": {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}})
    repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="venture://q3b/app")
    w = BuilderWorker(structured_output={"status": "done", "candidate_files": GOOD_CANDIDATE})
    build_runtime.execute_build(migrated, auth.action_id, registry=registry_with(w), worker_kind=w.kind,
                                verifier_kind="structured-contract", capability_scope=_CAPS,
                                timeout_seconds=60, max_attempts=1)
    out = build_runtime.capture_and_check_build(migrated, auth.action_id, substrate_release_id=rel.substrate_release_id)
    d = _mf(buyer="independent physiotherapy clinics", workflows=["ai chat triage -> escalate"],
            features=["ai_chat"], differentiators_implemented=["clinical triage model"], vocabulary=["triage", "clinic"])
    res = _assess(migrated, out["manifest"].build_manifest_id, d)
    assert res["dimensions"]["ANTIGENERIC"] == "PASS" and res["dimensions"]["PRODUCT"] == "PASS"


def test_4_wrong_buyer_product_and_commercial_fail(migrated):
    auth, mid = captured_manifest(migrated, "q4")
    d = _mf(buyer="corporate legal departments", workflows=["contract intake -> review -> approve"],
            features=["contract_review"], differentiators_implemented=[])
    out = _assess(migrated, mid, d)
    assert out["dimensions"]["PRODUCT"] == "FAIL" and out["dimensions"]["COMMERCIAL"] == "FAIL"


def test_5_missing_differentiator_fails(migrated):
    auth, mid = captured_manifest(migrated, "q5")
    out = _assess(migrated, mid, _mf(differentiators_implemented=[]))
    assert out["dimensions"]["PRODUCT"] == "FAIL" and out["dimensions"]["ANTIGENERIC"] == "FAIL"


def test_6_required_capability_absent_product_fail(migrated):
    auth, mid = captured_manifest(migrated, "q6")
    out = _assess(migrated, mid, _mf(features=["approval_tracking"]))
    assert out["dimensions"]["PRODUCT"] == "FAIL"


def test_7_excluded_capability_present_fails(migrated):
    auth, mid = captured_manifest(migrated, "q7")
    out = _assess(migrated, mid, _mf(features=["preauth_drafting", "approval_tracking", "generic_crm"]))
    assert out["dimensions"]["PRODUCT"] == "FAIL" and out["dimensions"]["COMMERCIAL"] == "FAIL"


def test_8_critical_dead_end_experience_fail(migrated):
    auth, mid = captured_manifest(migrated, "q8")
    out = _assess(migrated, mid, _mf(dead_ends=["draft pre-auth"]))
    assert out["dimensions"]["EXPERIENCE"] == "FAIL" and out["dimensions"]["PRODUCT"] == "PASS"


def test_9_missing_required_state_experience_fail(migrated):
    auth, mid = captured_manifest(migrated, "q9", extra_contract={"experience": {"required_states": ["error"]}})
    out = _assess(migrated, mid, _mf(states=["empty"]))
    assert out["dimensions"]["EXPERIENCE"] == "FAIL"


def test_10_coherent_domain_journey_experience_pass(migrated):
    auth, mid = captured_manifest(migrated, "q10")
    out = _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    assert out["dimensions"]["EXPERIENCE"] == "PASS"


def test_11_commercial_mechanism_reflected_pass(migrated):
    auth, mid = captured_manifest(migrated, "q11", extra_contract={"commercial": {"requires_conversion": True}})
    out = _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)  # has cta
    assert out["dimensions"]["COMMERCIAL"] == "PASS"


def test_12_commercial_contradiction_fail(migrated):
    auth, mid = captured_manifest(migrated, "q12")
    out = _assess(migrated, mid, _mf(features=["preauth_drafting", "approval_tracking", "generic_chatbot"]))
    assert out["dimensions"]["COMMERCIAL"] == "FAIL"


def test_13_missing_conversion_path_commercial_fail(migrated):
    auth, mid = captured_manifest(migrated, "q13", extra_contract={"commercial": {"requires_conversion": True}})
    out = _assess(migrated, mid, _mf(cta=[]))
    assert out["dimensions"]["COMMERCIAL"] == "FAIL"


# ==========================================================================
# Authority: worker/reviewer self-report inert (matrix 14, 15)
# ==========================================================================
def test_14_worker_claims_all_pass_inert(migrated):
    # The worker declares a passing product manifest is a lie: it omits a required
    # capability while asserting *_pass. The kernel derives FAIL from structure.
    auth = build_authority(migrated, slug="q14", key="q14")
    rel = make_substrate(migrated, key="rel-q14")
    freeze_default_build_spec(migrated, auth, expected_output_contract={
        "require": {"status": "done"},
        "technical": {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}})
    repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="venture://q14/app")
    bad_manifest = _mf(features=["approval_tracking"])  # missing preauth_drafting
    w = BuilderWorker(structured_output={
        "status": "done", "candidate_files": GOOD_CANDIDATE, "product_manifest": bad_manifest,
        "product_pass": True, "experience_pass": True, "commercial_pass": True,
        "antigeneric_pass": True, "overall_pass": True})
    build_runtime.execute_build(migrated, auth.action_id, registry=registry_with(w), worker_kind=w.kind,
                                verifier_kind="structured-contract", capability_scope=_CAPS,
                                timeout_seconds=60, max_attempts=1)
    build_runtime.capture_and_check_build(migrated, auth.action_id, substrate_release_id=rel.substrate_release_id)
    out = build_runtime.assess_build_quality(migrated, auth.action_id)  # reads worker manifest
    assert out["dimensions"]["PRODUCT"] == "FAIL" and out["overall"] == "FAIL"


def test_15_reviewer_verdict_inert_but_recorded_as_observation(migrated):
    auth, mid = captured_manifest(migrated, "q15")
    reviews = [{"dimension": "PRODUCT", "criterion_id": "looks_great", "observation": "polished",
                "interpretation": "PASS", "verdict": "PASS", "source_refs": ["screen1"]}]
    out = _assess(migrated, mid, _mf(features=["approval_tracking"]), review_observations=reviews)
    assert out["dimensions"]["PRODUCT"] == "FAIL"  # reviewer PASS ignored
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM build_quality_evidence WHERE build_manifest_id = %s AND source_type = 'REVIEW_OBSERVATION'",
            (mid,))
        assert cur.fetchone()[0] == 1


# ==========================================================================
# Overall derivation: any dimension FAIL blocks PASS; no compensation (matrix 16-21)
# ==========================================================================
def test_16_technical_fail_blocks_overall(migrated):
    # candidate omits the required file -> Technical FAIL; product manifest is good.
    auth, mid = captured_manifest(migrated, "q16", candidate_files=[{"path": "readme.txt", "content": "x"}])
    out = _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    assert all(v == "PASS" for v in out["dimensions"].values())
    from aidan_core.build import technical as technical_mod
    assert technical_mod.technical_verdict(migrated, mid) == "FAIL"
    assert out["overall"] == "FAIL"  # Technical FAIL cannot be compensated


@pytest.mark.parametrize("dim,descriptor", [
    ("PRODUCT", {"features": ["approval_tracking"]}),
    ("EXPERIENCE", {"dead_ends": ["x"]}),
    ("COMMERCIAL", {"buyer": "someone else"}),
    ("ANTIGENERIC", {"differentiators_implemented": [], "features": ["dashboard"]}),
])
def test_17_20_each_dimension_fail_blocks_overall(migrated, dim, descriptor):
    auth, mid = captured_manifest(migrated, f"q17-{dim}")
    out = _assess(migrated, mid, _mf(**descriptor))
    assert out["dimensions"][dim] == "FAIL" and out["overall"] == "FAIL"


def test_21_no_weighted_compensation(migrated):
    # three dimensions PASS cannot outweigh one FAIL
    auth, mid = captured_manifest(migrated, "q21")
    out = _assess(migrated, mid, _mf(features=["approval_tracking"]))  # only Product fails
    passes = sum(1 for v in out["dimensions"].values() if v == "PASS")
    assert passes == 3 and out["overall"] == "FAIL"


# ==========================================================================
# Append-only + idempotency + integrity (matrix 22-27, 33)
# ==========================================================================
def test_22_exact_evidence_replay_converges(migrated):
    auth, mid = captured_manifest(migrated, "q22")
    a = _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM build_quality_evidence WHERE build_manifest_id = %s", (mid,))
        n1 = cur.fetchone()[0]
    b = _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM build_quality_evidence WHERE build_manifest_id = %s", (mid,))
        n2 = cur.fetchone()[0]
    assert a == b and n1 == n2


def test_23_changed_evidence_conflicts(migrated):
    auth, mid = captured_manifest(migrated, "q23")
    _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    with pytest.raises(IdempotencyConflictError):
        _assess(migrated, mid, _mf(features=["approval_tracking"]))  # different evidence for same criteria


def test_24_evidence_append_only(migrated):
    auth, mid = captured_manifest(migrated, "q24")
    _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE build_quality_evidence SET evidence_hash = 'x' WHERE build_manifest_id = %s", (mid,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM build_quality_evidence WHERE build_manifest_id = %s", (mid,))


def test_25_assessment_append_only(migrated):
    auth, mid = captured_manifest(migrated, "q25")
    _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE build_quality_assessment SET verdict = 'FAIL' WHERE build_manifest_id = %s", (mid,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM build_quality_assessment WHERE build_manifest_id = %s", (mid,))


def test_26_27_cross_venture_evidence_rejected(migrated):
    auth_a, mid_a = captured_manifest(migrated, "q26a", key="A")
    auth_b, mid_b = captured_manifest(migrated, "q26b", key="B")
    # attach venture B to venture A's manifest -> composite FK violation
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO build_quality_evidence (venture_id, build_manifest_id, dimension, criterion, "
                "source_type, evidence_payload, evidence_hash) VALUES (%s, %s, 'PRODUCT', 'X', 'KERNEL_DERIVED', '{}', 'h')",
                (auth_b.venture_id, mid_a))


def test_33_no_scalar_score_column(migrated):
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name IN ('build_quality_assessment','build_quality_evidence') "
            "AND column_name IN ('score','quality_score','weight','confidence')")
        assert cur.fetchone()[0] == 0


# ==========================================================================
# Retry history + no side effects (matrix 28-32, 34)
# ==========================================================================
def test_28_29_attempt1_fail_attempt2_pass_history_preserved(migrated):
    rel = make_substrate(migrated, key="rel-q28")
    auth = build_authority(migrated, slug="q28", key="q28")
    freeze_default_build_spec(migrated, auth, expected_output_contract={
        "require": {"status": "done"},
        "technical": {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}})
    repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="venture://q28/app")
    worker = ScriptedBuilderWorker([
        {"status": "wrong", "candidate_files": GOOD_CANDIDATE},   # attempt 1 -> verification rejects
        {"status": "done", "candidate_files": GOOD_CANDIDATE},    # attempt 2
    ])
    reg = registry_with(worker)
    common = dict(worker_kind="builder-a", verifier_kind="structured-contract", capability_scope=_CAPS,
                  timeout_seconds=60, max_attempts=2)
    _, r1 = build_runtime.execute_build(migrated, auth.action_id, registry=reg, **common)
    cap1 = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, execution_attempt_id=r1.attempt_id)
    q1 = _assess(migrated, cap1["manifest"].build_manifest_id, _mf(differentiators_implemented=[]))  # FAIL
    factory_runtime.verify_and_complete(migrated, auth.action_id, actual_cost=0)  # reject attempt 1
    _, r2 = build_runtime.execute_build(migrated, auth.action_id, registry=reg, **common)
    cap2 = build_runtime.capture_and_check_build(
        migrated, auth.action_id, substrate_release_id=rel.substrate_release_id, execution_attempt_id=r2.attempt_id)
    q2 = _assess(migrated, cap2["manifest"].build_manifest_id, GOOD_PRODUCT_MANIFEST)  # PASS
    assert q1["dimensions"]["PRODUCT"] == "FAIL" and q2["overall"] == "PASS"
    # attempt-1 FAIL assessment remains
    assert quality_mod.dimension_verdict(migrated, cap1["manifest"].build_manifest_id, "PRODUCT") == "FAIL"
    assert cap1["manifest"].build_manifest_id != cap2["manifest"].build_manifest_id


def test_31_32_34_no_lifecycle_proof_or_provider(migrated):
    auth, mid = captured_manifest(migrated, "q31")
    _assess(migrated, mid, GOOD_PRODUCT_MANIFEST)
    assert _lifecycle(migrated, auth.venture_id) == "DISCOVERED"  # no lifecycle mutation
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s", (auth.action_id,))
        assert cur.fetchone()[0] == 0                            # Slice 3 records no proof
        cur.execute("SELECT to_regclass('public.deployment')")
        assert cur.fetchone()[0] is None
