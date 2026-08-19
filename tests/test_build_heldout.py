"""Gate 5 / Slice 4 — HELD-OUT builder-quality evals.

Separate file, distinct ventures/buyers/workflows/differentiators/vocabulary/repo
refs — NOT development fixtures renamed. Drives the SAME frozen Gate 5 production
runtime (via ``full_eval``) with no production special-casing. Proves the quality
system generalizes beyond the development matrix.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core.build import manifest as manifest_mod
from aidan_core.build import quality as quality_mod
from aidan_core.build import repository as repo_mod
from aidan_core.build import runtime as build_runtime
from aidan_core.factory import runtime as factory_runtime

from build_fakes import (
    GOOD_CANDIDATE,
    ScriptedBuilderWorker,
    build_authority,
    freeze_default_build_spec,
    full_eval,
    make_substrate,
    registry_with,
)

_CAPS = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]

# ---- distinct held-out venture profiles ---------------------------------------------
FREIGHT_SPEC = dict(
    buyer="regional freight brokerages", problem="empty backhauls waste carrier capacity",
    value_proposition="auto-books backhauls from lane history", product_category="vertical freight brokerage tool",
    primary_workflow="ingest load -> match lane -> price -> book backhaul",
    differentiators=["lane-history matching engine", "backhaul optimizer"],
    required_capabilities=["load_matching", "backhaul_booking"], excluded_capabilities=["generic_crm"])
FREIGHT_PM = dict(
    buyer="regional freight brokerages", workflows=["ingest load -> match lane -> price -> book backhaul"],
    features=["load_matching", "backhaul_booking"],
    differentiators_implemented=["lane-history matching engine", "backhaul optimizer"],
    vocabulary=["load", "carrier", "lane", "backhaul", "freight"], states=["empty", "loading", "error"],
    cta=["book_backhaul"], dead_ends=[])

DENTAL_SPEC = dict(
    buyer="independent dental practices", problem="no-shows and manual recall scheduling lose chair time",
    value_proposition="predictive recall scheduling that fills cancellations",
    product_category="vertical dental scheduling tool",
    primary_workflow="detect gap -> rank recall candidates -> auto-offer -> confirm",
    differentiators=["recall-propensity model", "chair-time optimizer"],
    required_capabilities=["recall_ranking", "gap_filling"], excluded_capabilities=["generic_crm"])

TRIAGE_SPEC = dict(
    buyer="community mental health clinics", problem="intake triage backlog delays urgent care",
    value_proposition="conversational intake that risk-stratifies and routes urgent cases",
    product_category="behavioral health intake tool",
    primary_workflow="intake chat -> risk stratify -> route urgent",
    differentiators=["validated risk-stratification model", "crisis escalation routing"],
    required_capabilities=["intake_chat", "risk_routing"], excluded_capabilities=["generic_crm"])
TRIAGE_PM = dict(
    buyer="community mental health clinics", workflows=["intake chat -> risk stratify -> route urgent"],
    features=["intake_chat", "risk_routing"],
    differentiators_implemented=["validated risk-stratification model", "crisis escalation routing"],
    vocabulary=["intake", "triage", "risk", "crisis"], states=["empty", "loading", "error"],
    cta=["start_intake"], dead_ends=[])

STUDIO_SPEC = dict(
    buyer="boutique fitness studios", problem="waitlist churn when classes fill and cancel late",
    value_proposition="dynamic waitlist promotion that fills late cancellations",
    product_category="vertical fitness studio ops tool",
    primary_workflow="detect cancellation -> rank waitlist -> promote -> confirm",
    differentiators=["waitlist-propensity model", "late-fill optimizer"],
    required_capabilities=["waitlist_ranking", "late_fill"], excluded_capabilities=["generic_crm"])
STUDIO_PM = dict(
    buyer="boutique fitness studios", workflows=["detect cancellation -> rank waitlist -> promote -> confirm"],
    features=["waitlist_ranking", "late_fill"],
    differentiators_implemented=["waitlist-propensity model", "late-fill optimizer"],
    vocabulary=["waitlist", "studio", "class", "cancellation"], states=["empty", "loading", "error"],
    cta=["promote_waitlist"], dead_ends=[])


def test_H1_differentiated_vertical_all_pass(migrated):
    r = full_eval(migrated, "hoH1", spec_overrides=FREIGHT_SPEC, product_manifest=FREIGHT_PM)
    assert r.technical == "PASS" and all(v == "PASS" for v in r.dimensions.values())
    assert r.overall == "PASS"


def test_H2_deceptively_polished_generic_template_fails(migrated):
    # technically strong, coherent structure, but generic relative to the dental workflow
    generic_pm = dict(
        buyer="independent dental practices", workflows=["generic admin dashboard -> list -> edit"],
        features=["dashboard", "kpi_cards", "crud", "settings", "profile"], differentiators_implemented=[],
        vocabulary=["dashboard", "records"], states=["empty", "loading", "error"], cta=["save"], dead_ends=[])
    r = full_eval(migrated, "hoH2", spec_overrides=DENTAL_SPEC, product_manifest=generic_pm)
    assert r.technical == "PASS"
    assert r.dimensions["ANTIGENERIC"] == "FAIL" and r.dimensions["PRODUCT"] == "FAIL"
    assert r.overall == "FAIL"


def test_H3_technically_valid_commercial_mismatch(migrated):
    # differentiated, coherent product — but the required conversion mechanism is absent
    r = full_eval(migrated, "hoH3", spec_overrides=STUDIO_SPEC,
                  contract_extra={"commercial": {"requires_conversion": True}},
                  product_manifest=dict(STUDIO_PM, cta=[]))
    assert r.dimensions["PRODUCT"] == "PASS" and r.dimensions["COMMERCIAL"] == "FAIL"
    assert r.overall == "FAIL"


def test_H4_critical_domain_workflow_omitted(migrated):
    # plausible but omits the key workflow step (no auto-offer/confirm -> workflow mismatch)
    partial_pm = dict(
        buyer="independent dental practices", workflows=["detect gap -> rank recall candidates"],
        features=["recall_ranking", "gap_filling"],
        differentiators_implemented=["recall-propensity model", "chair-time optimizer"],
        vocabulary=["recall", "dental", "chair"], states=["empty", "loading", "error"], cta=["offer"], dead_ends=[])
    r = full_eval(migrated, "hoH4", spec_overrides=DENTAL_SPEC, product_manifest=partial_pm)
    assert r.dimensions["PRODUCT"] == "FAIL" and r.overall == "FAIL"


def test_H5_corrected_second_attempt_history_preserved(migrated):
    rel = make_substrate(migrated, key="rel-hoH5")
    auth = build_authority(migrated, slug="hoH5", key="H5")
    freeze_default_build_spec(migrated, auth, expected_output_contract={
        "require": {"status": "done"},
        "technical": {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}},
        **FREIGHT_SPEC)
    repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="venture://hoH5/app")
    worker = ScriptedBuilderWorker([
        {"status": "wrong", "candidate_files": GOOD_CANDIDATE, "product_manifest": dict(FREIGHT_PM, dead_ends=["price"])},
        {"status": "done", "candidate_files": GOOD_CANDIDATE, "product_manifest": FREIGHT_PM}])
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
    assert quality_mod.dimension_verdict(migrated, cap1["manifest"].build_manifest_id, "EXPERIENCE") == "FAIL"
    assert quality_mod.overall_verdict(migrated, cap2["manifest"].build_manifest_id) == "PASS"
    assert cap1["manifest"].build_manifest_id != cap2["manifest"].build_manifest_id


def test_H6_cross_venture_contamination_rejected(migrated):
    a = full_eval(migrated, "hoH6a", key="H6a", spec_overrides=FREIGHT_SPEC, product_manifest=FREIGHT_PM)
    b = full_eval(migrated, "hoH6b", key="H6b", spec_overrides=TRIAGE_SPEC, product_manifest=TRIAGE_PM)
    # venture B's quality evidence cannot attach to venture A's manifest
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO build_quality_evidence (venture_id, build_manifest_id, dimension, criterion, "
                "source_type, evidence_payload, evidence_hash) VALUES (%s, %s, 'PRODUCT', 'X', 'KERNEL_DERIVED', '{}', 'h')",
                (b.auth.venture_id, a.manifest_id))


def test_H7_legitimate_chat_vertical_not_penalized(migrated):
    r = full_eval(migrated, "hoH7", spec_overrides=TRIAGE_SPEC, product_manifest=TRIAGE_PM)
    assert r.dimensions["ANTIGENERIC"] == "PASS" and all(v == "PASS" for v in r.dimensions.values())
    assert r.overall == "PASS"


def test_H8_strong_experience_weak_differentiator_fails(migrated):
    # coherent journey + rich states/vocab, but the differentiating mechanism is absent
    weak_pm = dict(FREIGHT_PM, differentiators_implemented=[])
    r = full_eval(migrated, "hoH8", spec_overrides=FREIGHT_SPEC, product_manifest=weak_pm)
    assert r.dimensions["EXPERIENCE"] == "PASS"
    assert r.dimensions["ANTIGENERIC"] == "FAIL" and r.dimensions["PRODUCT"] == "FAIL"
    assert r.overall == "FAIL"
