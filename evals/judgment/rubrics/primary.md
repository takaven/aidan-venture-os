# Primary Rubric — Judgment Evaluation

> **Status:** metric families, weights, and the objective/human split are **frozen** below. The
> numeric **PASS/AMBIGUOUS/FAIL thresholds and the cost-adjustment formula are NOT frozen yet** — they
> are pre-registered (committed to the repo) **before** the holdout is unsealed (see the placeholder
> at the end). All scoring is per case against that case's `hidden_ground_truth`, then aggregated.

## Weighting

- **Primary (objective-dominant): 75%** (band 70–80%).
- **Secondary (qualitative): 25%** (band 20–30%).

All weights below are **percent of the total** and sum to **100** (primary 75 + secondary 25). Objective
metrics dominate so an arm cannot win on prose. These weights are mirrored in `scorer/score.py`
(`WEIGHTS`) — keep the two in sync.

## Primary metrics — 75%

| # | Metric | Weight | Scored how | Objective / human |
|---|---|---|---|---|
| P1 | **Opportunity ranking** — does the arm's ranking match the ground-truth ranking? | 15% | Rank-correlation vs `hidden_ground_truth.ranking` (top-1 correct + rank distance). | **Objective / deterministic** |
| P2 | **Kill accuracy** — does it kill exactly the fatally-flawed opportunities and not the sound ones? | 15% | F1 of the arm's KILL set vs `hidden_ground_truth.fund_hold_kill`. | **Objective / deterministic** |
| P3 | **Critical-unknown identification** — does it name the one unknown the decision actually hinges on? | 12% | Overlap of the arm's stated critical unknown with `hidden_ground_truth.critical_unknown`; auto keyword-overlap proxy now, human tie-break later. | **Mostly objective; human tie-break** |
| P4 | **Experiment-discrimination quality** — is the proposed cheapest test one that would actually discriminate the critical unknown, within budget? | 10% | Vs `hidden_ground_truth.good_cheap_test`: within budget + targets the unknown + decision-changing. | **Human judgement (heuristic now)** |
| P5 | **Capital efficiency** — is spend within the case ceiling and directed at the highest decision-value action (not over-scaled)? | 11% | Deterministic ceiling check (fail if over `capital_ceiling_usd`) + allocation vs ground-truth Fund/Hold/Kill. | **Objective / deterministic** |
| P6 | **Calibration & updating** — (staged) does the final call end in the correct place after contradictory `T0→T1→T2` evidence, and is confidence proportionate? | 12% | Staged: final decision matches ground truth / expected update direction (deterministic). Non-staged: light confidence-calibration heuristic. | **Objective on staged direction; heuristic otherwise** |

## Secondary metrics — 25%

| # | Metric | Weight | Objective / human |
|---|---|---|---|
| S1 | **Evidence discipline** — no fabricated opportunities/facts; observation vs interpretation kept distinct. | 10% | Partly objective now (no invented opportunity ids is checkable); obs/interp distinction is human later. |
| S2 | **Decision usefulness & clarity** — an investor could act directly (clear Fund/Hold/Kill, rationale, next step). | 8% | Heuristic now (structural completeness); human later. |
| S3 | **Reasoning soundness** — no unforced logical errors, no seductive-narrative capture. | 7% | Placeholder now (neutral); human later. |

## Objective vs human judgement — summary

- **Deterministic (scorer computes directly from the key):** P1, P2, P5, the fabrication check in S1,
  and the staged-direction part of P6. These carry the majority of the primary weight and make the
  headline result reproducible.
- **Human judgement (independent rater, rubric-guided, blind to arm identity):** P4, S2, S3, and the
  tie-break portions of P3 and P6. Raters must not know which arm produced an output.

Where a metric is human-judged, the design intent is that the deterministic majority is sufficient to
detect a **material** difference between arms even if the human-judged minority is noisy.

## Cost adjustment

Each arm's per-case tokens/cost are recorded by the runners. The headline metric is
**quality-per-dollar** (raw rubric score adjusted by cost). The exact formula is part of the
pre-registration below — it must be frozen before the holdout is scored.

## Aggregation

Per case → weighted rubric score in [0, 1]. Per arm → mean (and dispersion) across the case set,
reported separately for development and holdout. The arm comparison is on the **cost-adjusted**
aggregate.

## PASS / AMBIGUOUS / FAIL thresholds — PLACEHOLDER (freeze before holdout)

> **TODO — PRE-REGISTER BEFORE UNSEALING THE HOLDOUT.** Do not invent these now. When frozen, fill in:
>
> - `material_lift_margin`: the minimum cost-adjusted advantage of T over each control to count as
>   "material" (not noise).
> - `cost_adjustment_formula`: exact quality-per-dollar definition.
> - `simplicity_kill_band`: how close C1 must be to T (cost-adjusted) to fire the simplicity kill.
> - `noise_band`: dispersion below which differences are treated as ties.
>
> The decision tree that consumes these is frozen in `../README.md` (PASS / AMBIGUOUS / FAIL +
> simplicity kill test). Only the numbers are pending.
