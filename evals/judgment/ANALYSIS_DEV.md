# Development evaluation — analysis & interpretation

Interpretation of the first live development run (raw numbers in `dev_summary.md`; machine-readable
detail in the gitignored `results/`). Model `claude-opus-4-8`, 7 development cases, all arms parsed
7/7.

## Headline result

| Arm | Mean weighted score | Cost (USD, approx) | Calls |
|---|---:|---:|---:|
| C1 — distilled-AIDAN (one pass) | **0.860** | ~0.21 | 7 |
| C0 — general (one pass) | **0.855** | ~0.22 | 7 |
| T — full AIDAN staged (7 calls/case) | **0.818** | ~1.39 | 49 |

**C1 ≈ C0 > T on quality, and T costs ≈ 6.7× C1.** The full staged treatment is both **lower-scoring**
and **far more expensive** than either single-pass control on this development set.

## Interpretation — the simplicity kill test is triggered

Per the harness's frozen design (`README.md`), the **simplicity kill test** fires when the cheap
distilled control (C1) captures essentially all of the treatment's benefit. Here C1 does not merely
match T — it slightly **exceeds** it, at ~1/7th the cost. On this evidence the **full 7-call staged
structure is not justified as-is**: the multi-call orchestration is buying negative quality at large
cost. The right direction is to **simplify** the treatment toward the smallest form that retains the
AIDAN distinctions, rather than to invest further in the 7-stage pipeline.

This is a first-class, expected outcome of the pivot: the harness was built to be able to kill the
structure, and on the development set it did.

## What this does and does not establish (confidence & limits)

Strong, low-caveat findings:
- **Cost gap is real and large** (deterministic token accounting): T ≈ 6.7× C1. Not sensitive to any
  scorer heuristic.
- **T did not outscore the cheap controls** on any aggregate cut here.
- On the deterministic metrics all three arms are strong and roughly tied: **P2 kill-accuracy = 1.00**
  and **P5 capital-efficiency = 1.00** for every arm; the model gets the kill/allocation calls right
  regardless of scaffolding.

Caveats that bound how hard to read the *quality ordering* (the ~0.04 spread):
- **Small sample:** one run, 7 cases. The score spread between arms (~0.04) is within the range a
  handful of cases could move.
- **Heuristic/placeholder metrics flatten and depress scores:** P3 (critical-unknown) is an automatic
  keyword-overlap proxy scoring low for all arms; P4/S2 are heuristics; S3 is a fixed 0.5 placeholder.
  Real human raters on P3/P4/S2/S3 could shift the *quality* ordering — but they cannot change the
  **cost** conclusion.
- No thresholds are applied and none are proposed (per plan). "Kill test triggered" here is a
  direction, not a frozen pass/fail verdict; thresholds get pre-registered before any holdout run.

**Net:** the cost verdict against the 7-call pipeline is solid; the precise quality ordering is
provisional pending real human scoring. Both point the same way — simplify T.

## Next step (recorded, not executed)

Before any holdout work: revise the T arm toward a cheaper form (see the design note below), re-run
the **development** set to compare the revised T against C0/C1, and only then decide whether a
structured arm still earns a place — and pre-register thresholds. Do **not** create holdout cases or
change scorer thresholds yet.

---

## Design note — one lightweight T revision to try (proposal only, not implemented)

**Problem being targeted:** T's cost is dominated by *orchestration*, not by reasoning — 7 separate
API round-trips per case, each re-sending the shared context, giving ~7× the tokens of a single pass.
The open question is whether the *forced decomposition* adds value independently of the expensive
*multi-call* delivery.

**Proposal: "T-compact" — the same decomposition in a single call.**
Collapse the seven sequential stage-calls into **one** structured prompt that requires the model to
work through the identical ordered sections **within one response**, under explicit headers:

1. Observations (evidence only)
2. Claims & contradictions (interpretation, flagged vs evidence)
3. Independent Kill Case (per opportunity, before upside)
4. Critical unknown
5. Cheapest discriminating test
6. Capital ranking under the ceiling → final Fund/Hold/Kill JSON (the scored artifact)

This **preserves every AIDAN distinction the treatment is meant to test** — evidence vs
interpretation, an independent Kill Case, the critical unknown, the cheapest discriminating test, and
capital ranking — while cutting delivery from 7 calls to **1** (context sent once). Expected cost
drops to roughly C1's level, which turns the comparison into the sharpest possible test: *does the
staged decomposition itself help, once the multi-call overhead is removed?*

- If T-compact ≈ C1: the decomposition adds nothing over a well-written one-pass doctrine prompt →
  the structure collapses into C1 and the "staged" arm is retired.
- If T-compact > C1: the *decomposition* was valuable and only the *multi-call orchestration* was
  waste → keep the decomposition, drop the round-trips.

Intentionally **one** alternative, and intentionally the cheapest structural change that still lets
the experiment answer its question. Not implemented in this session; recorded for the next
development iteration.
