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

# TODO: load_results(path) -> dict[arm, list[ArmResult]]
# TODO: load_answer_keys(cases_dir) -> dict[case_id, AnswerKey]   (holdout keys only at final scoring)
# TODO: score_objective(result, key) -> float          # 70-80% weight; per rubrics/primary.md
# TODO: score_secondary(result) -> float               # 20-30% weight
# TODO: cost_adjust(quality, cost) -> float             # quality-per-dollar; formula pre-registered
# TODO: aggregate(arm) -> ArmScore                      # mean cost-adjusted score across the case set
# TODO: material_lift(a, b) -> bool                     # is a's cost-adjusted edge over b material?
# TODO: decide(c0, c1, t) -> {"PASS"|"AMBIGUOUS"|"FAIL_SIMPLIFY", rationale}
#         PASS           : T beats BOTH C0 and C1 by a material cost-adjusted margin
#         AMBIGUOUS      : T beats C0 but not C1 materially, or within noise
#         FAIL_SIMPLIFY  : C1 (or C0) captures nearly all of T's benefit, or T not better
# TODO: simplicity_kill_test(c1, t) -> bool             # fires when C1 ~= T on cost-adjusted quality
# TODO: main(): score development set (harness validation) and, once frozen, the sealed holdout ONCE.


def main() -> int:
    raise NotImplementedError(
        "score is a skeleton; implement per evals/judgment/README.md + rubrics/primary.md before scoring")


if __name__ == "__main__":
    raise SystemExit(main())
