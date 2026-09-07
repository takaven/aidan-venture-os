"""Scorer for the AI-DAN Phase-1 judgment evaluation.

STATUS: SKELETON / PLACEHOLDER. No real scoring logic yet. See ../README.md and ../rubrics/primary.md
for the frozen design.

This scorer will:
  * load each arm's run results (C0, C1, T) and each case's sealed answer key;
  * apply the primary rubric (objective metrics 70-80% + secondary 20-30%);
  * apply the cost adjustment (headline metric is quality-per-dollar, not raw quality);
  * apply the PRECOMMITTED PASS / AMBIGUOUS / FAIL decision tree;
  * apply the SIMPLICITY KILL TEST: if C1 captures nearly all of T's cost-adjusted benefit -> SIMPLIFY.

Frozen-design guardrails (must hold when implemented):
  * thresholds/weights are pre-registered (committed) BEFORE the holdout is scored — no post-hoc tuning;
  * development and holdout are scored separately; the holdout is scored ONCE;
  * objective metrics dominate (70-80%); prose cannot win.
"""
from __future__ import annotations

# Ground truth: each case's `hidden_ground_truth` (cases/development/*.json) provides
# `ranking`, `fund_hold_kill`, `kills`, `critical_unknown`, `good_cheap_test`, and for staged cases
# `expected_update_direction` (UP/DOWN). Arm outputs are the STRICT shared JSON emitted by the runners.
#
# TODO: load_results(path) -> dict[arm, list[ArmResult]]
# TODO: load_ground_truth(cases_dir) -> dict[case_id, GroundTruth]   (holdout keys only at final scoring)
# Primary metrics (75%; rubrics/primary.md) — deterministic ones computed here:
# TODO: p1_ranking(out, gt)            -> float   # rank-correlation vs gt.ranking (top-1 + distance)
# TODO: p2_kill_accuracy(out, gt)      -> float   # precision/recall of KILL set vs gt.fund_hold_kill
# TODO: p3_critical_unknown(out, gt)   -> float   # match to gt.critical_unknown (human tie-break)
# TODO: p4_experiment_quality(out, gt) -> float   # vs gt.good_cheap_test (human, rubric-guided)
# TODO: p5_capital_efficiency(out, gt, ceiling) -> float  # ceiling check + allocation vs gt
# TODO: p6_calibration_updating(out, gt) -> float # staged: final call moved gt.expected_update_direction
# Secondary metrics (25%): TODO s1_evidence_discipline / s2_usefulness / s3_reasoning (human-guided)
# TODO: case_score(out, gt) -> float in [0,1]  (weighted per rubrics/primary.md)
# TODO: cost_adjust(quality, cost) -> float     # quality-per-dollar; formula PRE-REGISTERED before holdout
# TODO: aggregate(arm) -> ArmScore              # mean + dispersion of cost-adjusted score across cases
# TODO: material_lift(a, b) -> bool             # a's cost-adjusted edge over b exceeds frozen margin
# TODO: simplicity_kill_test(c1, t) -> bool     # fires when C1 ~= T on cost-adjusted quality -> SIMPLIFY
# TODO: decide(c0, c1, t) -> {"PASS"|"AMBIGUOUS"|"FAIL_SIMPLIFY", rationale}
#         PASS          : T beats BOTH C0 and C1 by a material cost-adjusted margin
#         AMBIGUOUS     : T beats C0 but not C1 materially, or within noise
#         FAIL_SIMPLIFY : C1 (or C0) captures nearly all of T's benefit, or T not better cost-adjusted
# TODO: main(): score development set (harness validation) and, once thresholds are frozen, the sealed
#       holdout ONCE. Thresholds/cost formula must be pre-registered (rubrics/primary.md) before holdout.


def main() -> int:
    raise NotImplementedError(
        "score is a skeleton; implement per evals/judgment/README.md + rubrics/primary.md before scoring")


if __name__ == "__main__":
    raise SystemExit(main())
