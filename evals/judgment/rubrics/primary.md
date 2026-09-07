# Primary Rubric — Judgment Evaluation

> **Status:** PLACEHOLDER / design skeleton. The metric *categories* and the objective-dominant split
> are frozen; the exact per-metric definitions, weights, and numeric thresholds are **TODO** and must
> be pre-registered (committed) **before** the holdout is scored. See `../README.md`.

## Weighting (frozen split)

- **Objective / verifiable metrics: 70–80%** of the score.
- **Secondary / qualitative metrics: 20–30%** of the score.

Objective metrics dominate so the result cannot be won on prose quality or document completeness.

## Objective metrics (70–80%) — scored against a per-case answer key

> TODO: define exact scoring (0/1 or graded) and weights for each. Each metric must be checkable
> against the case's sealed answer key, not judged subjectively.

- [ ] **Decision correctness** — did the arm reach the correct GO / KILL / DEFER decision?
- [ ] **Kill discipline** — did it correctly kill the weak/seductive-but-weak cases and not kill the
      genuinely attractive ones?
- [ ] **Cheapest-decisive-action identification** — did it name the lowest-cost action that would
      materially change the capital decision (capital-scarcity doctrine, ADR-037)?
- [ ] **Capital within scarcity frame** — is the proposed spend within the case's stated scarce frame
      ($20–$2,000 class), not an over-scaled ask?
- [ ] **Evidence grounding** — are the load-bearing claims traceable to the frozen evidence pack (no
      fabricated facts / hallucinated evidence)?
- [ ] **Assumption / Kill-Case surfacing** — did it surface the decisive assumptions and the condition
      that would falsify the opportunity?

## Secondary metrics (20–30%) — qualitative

> TODO: define light rubric + weights. Kept minority so they cannot dominate.

- [ ] Clarity and decision-usefulness of the rationale.
- [ ] Calibration of stated confidence to evidence strength.
- [ ] Absence of unforced reasoning errors.

## Cost adjustment (required)

> TODO: define the exact cost-adjustment formula. The headline comparison is **quality per dollar**,
> not raw quality. Every arm's tokens/cost per case are recorded by the runners and combined with the
> rubric score here.

## Answer keys

Each case under `cases/` ships (or will ship) a sealed answer key encoding the objective-metric
ground truth. **TODO:** define the answer-key schema. Answer keys for `holdout/` must never be read
during harness development.
