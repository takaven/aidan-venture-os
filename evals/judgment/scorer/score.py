"""Scorer for the AI-DAN Phase-1 judgment evaluation (development cases).

Scores a model's shared-schema output against a case's `hidden_ground_truth` using the rubric in
../rubrics/primary.md. Objective/deterministic metrics (P1 ranking, P2 kill accuracy, P5 capital
efficiency, staged part of P6, part of S1) are computed directly; human-judged metrics (P4, S2, S3,
and the near-miss parts of P3/P6) use transparent heuristics for now and are flagged so they can be
replaced by human raters before any holdout scoring.

Produces per-case, per-arm, and aggregate results, and carries token/cost through for the
cost-adjusted comparison. NO numeric PASS/AMBIGUOUS/FAIL thresholds are applied here — that decision
is deferred until the development numbers are reviewed and thresholds are pre-registered.
"""
from __future__ import annotations

import re

# Weights = percent of total; MUST mirror ../rubrics/primary.md. Sum = 100 (primary 75 + secondary 25).
WEIGHTS = {"P1": 15, "P2": 15, "P3": 12, "P4": 10, "P5": 11, "P6": 12, "S1": 10, "S2": 8, "S3": 7}

_STOP = {"the", "a", "an", "of", "to", "is", "it", "and", "or", "for", "in", "on", "at", "be", "will",
         "whether", "that", "this", "with", "by", "as", "are", "not", "no", "do", "does", "if", "into",
         "would", "could", "vs", "than", "actually", "any", "one", "its", "their", "they", "them"}


def _tokens(s) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", str(s or "").lower()) if w and w not in _STOP and len(w) > 2}


def _overlap(a, b) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# individual metrics -> (score in [0,1], detail dict)
# ---------------------------------------------------------------------------
def _decisions(out: dict) -> dict:
    return {str(o.get("id")): str(o.get("decision", "")).upper() for o in out.get("opportunities", [])}


def p1_ranking(out, gt) -> tuple[float, dict]:
    pred = [str(x) for x in out.get("ranking", [])]
    true = [str(x) for x in gt.get("ranking", [])]
    if not true:
        return 0.0, {"method": "deterministic", "note": "no ground-truth ranking"}
    if len(true) == 1:
        s = 1.0 if true[0] in pred[:1] else (0.5 if true[0] in pred else 0.0)
        return s, {"method": "deterministic", "single_opportunity": True}
    top1 = 1.0 if pred[:1] == true[:1] else 0.0
    # normalized rank displacement over ids present in ground truth
    pred_rank = {cid: i for i, cid in enumerate(pred)}
    n = len(true)
    max_disp = sum(abs(i - (n - 1 - i)) for i in range(n)) or 1
    disp = sum(abs(i - pred_rank.get(cid, n)) for i, cid in enumerate(true))
    order = max(0.0, 1.0 - disp / max_disp)
    return round(0.5 * top1 + 0.5 * order, 4), {"method": "deterministic", "top1": top1, "order": round(order, 3)}


def p2_kill_accuracy(out, gt) -> tuple[float, dict]:
    pred = {cid for cid, d in _decisions(out).items() if d == "KILL"}
    true = {cid for cid, v in gt.get("fund_hold_kill", {}).items() if str(v).upper() == "KILL"}
    if not pred and not true:
        return 1.0, {"method": "deterministic", "note": "no kills expected or predicted"}
    tp = len(pred & true)
    prec = tp / len(pred) if pred else 0.0
    rec = tp / len(true) if true else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return round(f1, 4), {"method": "deterministic", "pred_kill": sorted(pred), "true_kill": sorted(true),
                          "precision": round(prec, 3), "recall": round(rec, 3)}


def p3_critical_unknown(out, gt) -> tuple[float, dict]:
    ov = _overlap(out.get("critical_unknown"), gt.get("critical_unknown"))
    return round(min(1.0, ov * 2.0), 4), {"method": "auto_heuristic_needs_human", "keyword_overlap": round(ov, 3)}


def p4_experiment_quality(out, gt, ceiling) -> tuple[float, dict]:
    test = out.get("cheapest_discriminating_test") or {}
    desc = test.get("description", "")
    present = 1.0 if _tokens(desc) else 0.0
    try:
        cost = float(test.get("est_cost_usd", 0) or 0)
        within = 1.0 if 0 <= cost <= float(ceiling or 0) else 0.0
    except (TypeError, ValueError):
        within = 0.0
    ov = _overlap(desc, gt.get("good_cheap_test"))
    score = 0.4 * present + 0.3 * within + 0.3 * min(1.0, ov * 2.0)
    return round(score, 4), {"method": "heuristic_needs_human", "present": present, "within_budget": within,
                             "keyword_overlap": round(ov, 3)}


def p5_capital_efficiency(out, gt, ceiling) -> tuple[float, dict]:
    alloc = {str(a.get("id")): float(a.get("allocate_usd", 0) or 0) for a in out.get("capital_allocation", [])}
    total = sum(alloc.values())
    try:
        ceiling = float(ceiling or 0)
    except (TypeError, ValueError):
        ceiling = 0.0
    ceiling_ok = total <= ceiling + 1e-9
    if not ceiling_ok:
        return 0.0, {"method": "deterministic", "ceiling_violation": True, "total_allocated": total, "ceiling": ceiling}
    fh = {cid: str(v).upper() for cid, v in gt.get("fund_hold_kill", {}).items()}
    fund_ids = {c for c, v in fh.items() if v == "FUND"}
    kill_ids = {c for c, v in fh.items() if v == "KILL"}
    if not fund_ids:  # correct behaviour is to hold ~all capital as reserve
        align = 1.0 - min(1.0, (total / ceiling) if ceiling else 1.0)
    else:
        to_fund = sum(v for c, v in alloc.items() if c in fund_ids)
        to_kill = sum(v for c, v in alloc.items() if c in kill_ids)
        if total <= 0:
            align = 0.3  # should have funded something; funded nothing
        else:
            align = max(0.0, (to_fund - to_kill) / total)
    return round(align, 4), {"method": "deterministic", "total_allocated": total, "ceiling": ceiling}


def p6_calibration_updating(out, gt, staged) -> tuple[float, dict]:
    fh = {cid: str(v).upper() for cid, v in gt.get("fund_hold_kill", {}).items()}
    dec = _decisions(out)
    if staged:
        # single-opportunity staged cases: did the final call end where the evidence should land it?
        matches = [1.0 if dec.get(cid) == v else 0.0 for cid, v in fh.items()]
        s = sum(matches) / len(matches) if matches else 0.0
        return round(s, 4), {"method": "deterministic_staged_direction",
                             "expected_update_direction": gt.get("expected_update_direction")}
    # non-staged: light confidence-calibration heuristic
    confs = [o.get("confidence") for o in out.get("opportunities", []) if isinstance(o.get("confidence"), (int, float))]
    in_range = all(0.0 <= c <= 1.0 for c in confs) and bool(confs)
    top_conf = isinstance(out.get("confidence"), (int, float)) and 0.0 <= out.get("confidence") <= 1.0
    return (1.0 if (in_range and top_conf) else 0.5 if (in_range or top_conf) else 0.0), \
        {"method": "heuristic_needs_human"}


def s1_evidence_discipline(out, case) -> tuple[float, dict]:
    valid_ids = {str(o["id"]) for o in case.get("opportunities", [])}
    used = set()
    for o in out.get("opportunities", []):
        used.add(str(o.get("id")))
    used |= {str(x) for x in out.get("ranking", [])}
    used |= {str(a.get("id")) for a in out.get("capital_allocation", [])}
    used.discard("None")
    invented = used - valid_ids
    score = 1.0 if not invented else max(0.0, 1.0 - len(invented) / max(1, len(used)))
    return round(score, 4), {"method": "partial_auto_needs_human", "invented_ids": sorted(invented)}


def s2_usefulness(out) -> tuple[float, dict]:
    have = [bool(out.get("overall_recommendation")), bool(out.get("opportunities")),
            bool((out.get("cheapest_discriminating_test") or {}).get("description")),
            all(o.get("decision") for o in out.get("opportunities", []))]
    return round(sum(1 for x in have if x) / len(have), 4), {"method": "placeholder_heuristic"}


def s3_reasoning(out) -> tuple[float, dict]:
    return 0.5, {"method": "placeholder_neutral_needs_human"}


# ---------------------------------------------------------------------------
# case + arm + report scoring
# ---------------------------------------------------------------------------
def score_case(result: dict, case: dict) -> dict:
    """result: an ArmResult.to_json(); case: the full case dict (with hidden_ground_truth)."""
    gt = case.get("hidden_ground_truth", {})
    ceiling = case.get("capital_ceiling_usd")
    staged = bool(case.get("staged"))
    out = result.get("decision")
    if not result.get("ok") or not isinstance(out, dict):
        metrics = {k: {"score": 0.0, "detail": {"note": "no parseable output"}} for k in WEIGHTS}
        weighted = 0.0
    else:
        raw = {
            "P1": p1_ranking(out, gt),
            "P2": p2_kill_accuracy(out, gt),
            "P3": p3_critical_unknown(out, gt),
            "P4": p4_experiment_quality(out, gt, ceiling),
            "P5": p5_capital_efficiency(out, gt, ceiling),
            "P6": p6_calibration_updating(out, gt, staged),
            "S1": s1_evidence_discipline(out, case),
            "S2": s2_usefulness(out),
            "S3": s3_reasoning(out),
        }
        metrics = {k: {"score": v[0], "detail": v[1]} for k, v in raw.items()}
        weighted = round(sum(metrics[k]["score"] * WEIGHTS[k] for k in WEIGHTS) / 100.0, 4)
    return {"case_id": result.get("case_id"), "arm": result.get("arm"), "ok": result.get("ok"),
            "weighted_score": weighted, "metrics": metrics, "cost": result.get("cost")}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def score_arm(case_scores: list[dict]) -> dict:
    total_in = sum((c.get("cost") or {}).get("input_tokens", 0) for c in case_scores)
    total_out = sum((c.get("cost") or {}).get("output_tokens", 0) for c in case_scores)
    costs = [(c.get("cost") or {}).get("cost_usd") for c in case_scores]
    total_cost = None if any(x is None for x in costs) else round(sum(x or 0 for x in costs), 6)
    calls = sum((c.get("cost") or {}).get("calls", 0) for c in case_scores)
    mean_q = _mean([c["weighted_score"] for c in case_scores])
    # cost-adjusted headline is deferred (needs the pre-registered formula); expose the inputs.
    return {"mean_weighted_score": mean_q, "n_cases": len(case_scores),
            "n_ok": sum(1 for c in case_scores if c.get("ok")),
            "input_tokens": total_in, "output_tokens": total_out, "cost_usd": total_cost, "calls": calls,
            "per_metric_mean": {k: _mean([c["metrics"][k]["score"] for c in case_scores]) for k in WEIGHTS}}


def build_report(all_case_scores: list[dict]) -> dict:
    arms = sorted({c["arm"] for c in all_case_scores})
    report = {"weights": WEIGHTS, "arms": {}, "note": "No PASS/AMBIGUOUS/FAIL thresholds applied — "
              "development numbers first, then pre-register thresholds before holdout."}
    for arm in arms:
        cs = [c for c in all_case_scores if c["arm"] == arm]
        report["arms"][arm] = {"summary": score_arm(cs), "cases": cs}
    return report


def main() -> int:
    raise SystemExit("Use run_dev.py to score all arms/cases; score is a library module.")


if __name__ == "__main__":
    main()
