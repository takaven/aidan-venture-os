"""Venture product-quality gates — Product / Experience / Commercial / AntiGeneric
(Gate 5 Slice 3).

Five DISTINCT dimensions decide whether a built candidate is acceptable: Technical
(reused from Slice 2) plus the four added here. There is no scalar or weighted
score and no compensation between dimensions — overall quality is PASS iff ALL five
dimensions PASS, derived in the kernel.

Verdicts are kernel-derived from evidence, never self-certified. A builder's
`*_pass` claims are inert. A model reviewer may only contribute REVIEW_OBSERVATION
evidence (observation + interpretation), never a verdict. The candidate's declared
product structure (workflows, features, vocabulary, CTA, differentiators
implemented) is worker-declared STRUCTURAL_MANIFEST evidence, judged
DETERMINISTICALLY against the FROZEN build_spec; deeper code-derivation of that
structure is deferred. Commercial quality tests implementation alignment to the
already-approved Gate 2/3 thesis — never real demand, revenue, or PMF.
"""
from __future__ import annotations

import re
from typing import Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import IdempotencyConflictError, NotFoundError
from . import spec as build_spec_mod
from . import technical as technical_mod

DIMENSIONS = ("PRODUCT", "EXPERIENCE", "COMMERCIAL", "ANTIGENERIC")

# Generic patterns that only count AGAINST the candidate when they substitute for
# venture-specific functionality (never a sole keyword ban — always relative to spec).
GENERIC_PATTERNS = frozenset({
    "dashboard", "kpi", "kpi_cards", "kpis", "crud", "settings", "profile", "chat",
    "chatbot", "ai_chat", "landing", "pricing", "pricing_page", "onboarding", "navbar", "navigation",
})
_STOPWORDS = frozenset({"the", "a", "an", "and", "or", "of", "to", "for", "per", "with", "from", "into", "->"})


def _n(s) -> str:
    return str(s).strip().lower()


def _nset(items) -> set:
    return {_n(x) for x in (items or [])}


def _tokens(text) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", _n(text)) if t and t not in _STOPWORDS}


def _spec_dict(row) -> dict:
    return {
        "venture_id": row[1], "buyer": row[6], "problem": row[7], "value_proposition": row[8],
        "product_category": row[9], "primary_workflow": row[10], "differentiators": list(row[11] or []),
        "required_capabilities": list(row[12] or []), "excluded_capabilities": list(row[13] or []),
        "experience_principles": list(row[14] or []), "contract": dict(row[15] or {}),
    }


def _d(descriptor, key) -> list:
    return list((descriptor or {}).get(key, []))


# ---- deterministic per-dimension evaluators (criterion, PASS/FAIL, detail) ----------
def evaluate_product(spec, descriptor) -> list:
    features = _nset(_d(descriptor, "features"))
    workflows = _nset(_d(descriptor, "workflows"))
    diffs_impl = _nset(_d(descriptor, "differentiators_implemented"))
    req = _nset(spec["required_capabilities"])
    exc = _nset(spec["excluded_capabilities"])
    diffs = _nset(spec["differentiators"])
    missing_caps = sorted(req - features)
    present_excl = sorted(exc & features)
    missing_diffs = sorted(diffs - diffs_impl)
    return [
        ("PRIMARY_WORKFLOW_IMPLEMENTED", _n(spec["primary_workflow"]) in workflows,
         {"primary_workflow": spec["primary_workflow"]}),
        ("REQUIRED_CAPABILITIES_IMPLEMENTED", not missing_caps, {"missing": missing_caps}),
        ("EXCLUSIONS_RESPECTED", not present_excl, {"present_excluded": present_excl}),
        ("DIFFERENTIATORS_IMPLEMENTED", not missing_diffs, {"missing": missing_diffs}),
    ]


def evaluate_experience(spec, descriptor) -> list:
    workflows = _nset(_d(descriptor, "workflows"))
    dead_ends = _d(descriptor, "dead_ends")
    states = _nset(_d(descriptor, "states"))
    vocab_tokens = set().union(*[_tokens(v) for v in _d(descriptor, "vocabulary")]) if _d(descriptor, "vocabulary") else set()
    domain_tokens = _tokens(spec["buyer"]) | _tokens(spec["problem"]) | _tokens(spec["product_category"])
    required_states = _nset(spec["contract"].get("experience", {}).get("required_states", []))
    missing_states = sorted(required_states - states)
    wf_ok = _n(spec["primary_workflow"]) in workflows
    return [
        ("PRIMARY_JOURNEY_COMPLETE", wf_ok and not dead_ends, {"dead_ends": dead_ends}),
        ("CRITICAL_DEAD_END_FREE", not dead_ends, {"dead_ends": dead_ends}),
        ("DOMAIN_LANGUAGE_PRESENT", bool(vocab_tokens & domain_tokens),
         {"shared": sorted(vocab_tokens & domain_tokens)}),
        ("STATE_COVERAGE", not missing_states, {"missing_states": missing_states}),
    ]


def evaluate_commercial(spec, descriptor) -> list:
    features = _nset(_d(descriptor, "features"))
    exc = _nset(spec["excluded_capabilities"])
    commercial = spec["contract"].get("commercial", {})
    requires_conversion = bool(commercial.get("requires_conversion", False))
    cta = _d(descriptor, "cta")
    buyer_ok = _n(descriptor.get("buyer", "")) == _n(spec["buyer"])
    conversion_ok = (not requires_conversion) or bool(cta)
    contradiction = sorted(exc & features)
    return [
        ("BUYER_MATCH", buyer_ok, {"spec_buyer": spec["buyer"], "candidate_buyer": descriptor.get("buyer")}),
        ("CONVERSION_PATH_PRESENT", conversion_ok, {"required": requires_conversion, "cta": cta}),
        ("NO_OFFER_CONTRADICTION", not contradiction, {"contradicting_features": contradiction}),
    ]


def evaluate_antigeneric(spec, descriptor) -> list:
    features = _nset(_d(descriptor, "features"))
    workflows = _nset(_d(descriptor, "workflows"))
    diffs_impl = _nset(_d(descriptor, "differentiators_implemented"))
    req = _nset(spec["required_capabilities"])
    diffs = _nset(spec["differentiators"])
    wf_ok = _n(spec["primary_workflow"]) in workflows
    diff_ok = (not diffs) or diffs <= diffs_impl
    nongeneric = features - GENERIC_PATTERNS
    substance = bool(req <= features) and (bool(nongeneric) or bool(diffs & diffs_impl))
    return [
        ("ANTIGENERIC_WORKFLOW_SPECIFIC", wf_ok, {"primary_workflow": spec["primary_workflow"]}),
        ("ANTIGENERIC_DIFFERENTIATOR_PRESENT", diff_ok, {"missing": sorted(diffs - diffs_impl)}),
        ("ANTIGENERIC_NON_SUBSTITUTE", substance,
         {"nongeneric_features": sorted(nongeneric), "differentiators_implemented": sorted(diffs_impl)}),
    ]


_EVALUATORS = {
    "PRODUCT": evaluate_product, "EXPERIENCE": evaluate_experience,
    "COMMERCIAL": evaluate_commercial, "ANTIGENERIC": evaluate_antigeneric,
}


def _load(cur, build_manifest_id):
    cur.execute("SELECT venture_id, build_spec_id FROM build_manifest WHERE id = %s", (build_manifest_id,))
    m = cur.fetchone()
    if m is None:
        raise NotFoundError(f"build_manifest {build_manifest_id} does not exist")
    venture_id, build_spec_id = m
    cur.execute(
        "SELECT id, venture_id, action_request_id, source_investment_decision_id, source_recommendation_id, "
        "opportunity_id, buyer, problem, value_proposition, product_category, primary_workflow, "
        "differentiators, required_capabilities, excluded_capabilities, experience_principles, "
        "expected_output_contract, spec_hash, created_at FROM build_spec WHERE id = %s", (build_spec_id,))
    spec_row = cur.fetchone()
    return venture_id, _spec_dict(spec_row)


def run_quality_assessment(
    conn, build_manifest_id: str, *, product_descriptor: Optional[dict] = None,
    review_observations=(), actor: str = "factory",
) -> dict:
    """Evaluate the four product-quality dimensions, persist evidence + per-dimension
    verdicts (append-only, deterministic), and return dimension verdicts + overall.

    ``product_descriptor`` is the candidate's declared structure (STRUCTURAL_MANIFEST).
    ``review_observations`` are bounded reviewer observations (REVIEW_OBSERVATION);
    any verdict field on them is ignored.
    """
    descriptor = dict(product_descriptor or {})
    with conn.cursor() as cur:
        venture_id, spec = _load(cur, build_manifest_id)
        cur.execute(
            "SELECT dimension, criterion, evidence_hash FROM build_quality_evidence WHERE build_manifest_id = %s",
            (build_manifest_id,))
        existing_ev = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        cur.execute(
            "SELECT dimension, verdict FROM build_quality_assessment WHERE build_manifest_id = %s",
            (build_manifest_id,))
        existing_as = {r[0]: r[1] for r in cur.fetchall()}

    verdicts = {}
    for dimension in DIMENSIONS:
        criteria = _EVALUATORS[dimension](spec, descriptor)
        for criterion, ok, detail in criteria:
            payload = {"result": "PASS" if ok else "FAIL", "detail": detail}
            ev_hash = canonical_payload_hash(
                {"m": str(build_manifest_id), "dim": dimension, "crit": criterion, "src": "KERNEL_DERIVED",
                 "payload": payload})
            key = (dimension, criterion)
            if key in existing_ev:
                if existing_ev[key] != ev_hash:
                    raise IdempotencyConflictError(
                        f"quality evidence for {dimension}/{criterion} already exists with different content")
                continue
            with db.transaction(conn) as cur:
                cur.execute(
                    "INSERT INTO build_quality_evidence "
                    "(venture_id, build_manifest_id, dimension, criterion, source_type, evidence_payload, evidence_hash) "
                    "VALUES (%s, %s, %s, %s, 'KERNEL_DERIVED', %s, %s) "
                    "ON CONFLICT (build_manifest_id, dimension, criterion) DO NOTHING",
                    (venture_id, build_manifest_id, dimension, criterion, Json(payload), ev_hash))

        verdict = "PASS" if all(ok for _c, ok, _d in criteria) else "FAIL"
        basis_hash = canonical_payload_hash(
            {"dim": dimension, "criteria": [[c, bool(ok)] for c, ok, _ in criteria]})
        if dimension in existing_as:
            verdict = existing_as[dimension]              # persisted verdict is immutable-first
        else:
            with db.transaction(conn) as cur:
                cur.execute(
                    "INSERT INTO build_quality_assessment "
                    "(venture_id, build_manifest_id, dimension, verdict, decision_basis_hash) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (build_manifest_id, dimension) DO NOTHING",
                    (venture_id, build_manifest_id, dimension, verdict, basis_hash))
        verdicts[dimension] = verdict

    # Bounded reviewer observations are evidence only — never a verdict.
    for obs in review_observations or []:
        dimension = obs["dimension"]
        criterion = f"REVIEW::{obs['criterion_id']}"
        payload = {"observation": obs.get("observation"), "interpretation": obs.get("interpretation"),
                   "source_refs": obs.get("source_refs", [])}
        ev_hash = canonical_payload_hash(
            {"m": str(build_manifest_id), "dim": dimension, "crit": criterion, "src": "REVIEW_OBSERVATION",
             "payload": payload})
        if (dimension, criterion) in existing_ev:
            continue
        with db.transaction(conn) as cur:
            cur.execute(
                "INSERT INTO build_quality_evidence "
                "(venture_id, build_manifest_id, dimension, criterion, source_type, evidence_payload, evidence_hash) "
                "VALUES (%s, %s, %s, %s, 'REVIEW_OBSERVATION', %s, %s) "
                "ON CONFLICT (build_manifest_id, dimension, criterion) DO NOTHING",
                (venture_id, build_manifest_id, dimension, criterion, Json(payload), ev_hash))

    overall = overall_verdict(conn, build_manifest_id)
    with db.transaction(conn) as cur:
        audit.record_event(
            cur, event_type="build.quality_assessed", actor=actor, venture_id=venture_id, action_id=None,
            payload={"build_manifest_id": str(build_manifest_id), "dimensions": verdicts, "overall": overall})
    return {"dimensions": verdicts, "overall": overall}


def dimension_verdict(conn, build_manifest_id: str, dimension: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT verdict FROM build_quality_assessment WHERE build_manifest_id = %s AND dimension = %s",
            (build_manifest_id, dimension))
        row = cur.fetchone()
    return row[0] if row else "PENDING"


def overall_verdict(conn, build_manifest_id: str) -> str:
    """Overall PASS iff Technical (Slice 2) AND all four Slice-3 dimensions PASS.

    Derived — never stored as independent truth and never caller-supplied. No scalar,
    no weighting, no compensation between dimensions.
    """
    technical = technical_mod.technical_verdict(conn, build_manifest_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dimension, verdict FROM build_quality_assessment WHERE build_manifest_id = %s",
            (build_manifest_id,))
        rows = {r[0]: r[1] for r in cur.fetchall()}
    all_verdicts = [technical] + [rows.get(d, "PENDING") for d in DIMENSIONS]
    if any(v == "FAIL" for v in all_verdicts):
        return "FAIL"
    if all(v == "PASS" for v in all_verdicts):
        return "PASS"
    return "PENDING"
