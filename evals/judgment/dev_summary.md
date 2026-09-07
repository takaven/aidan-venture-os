# Development judgment eval — live run summary (2026-09-07)

Sanitized summary of the first **live** development run of the Phase-1 judgment harness. Committable
(no raw model prose, no secrets). Full machine-readable output and raw model text live in the
gitignored `results/` directory.

- **Command:** `python evals/judgment/run_dev.py --mode live --model claude-opus-4-8`
- **Model (all arms):** `claude-opus-4-8` · **Cases:** 7 development · **Arms:** C0, C1, T
- **Parse success:** 7/7 for every arm (no parse failures in the accepted run)

> **No PASS/AMBIGUOUS/FAIL verdict is applied and no thresholds are proposed here** — per the plan,
> we look at development numbers first, then pre-register thresholds before any holdout work. The
> figures below are raw measurements, not a decision.

## Arm summary

| Arm | Mean weighted score | OK/cases | Calls | Input tok | Output tok | Cost (USD, approx) |
|---|---|---|---|---|---|---|
| C0 (general) | **0.855** | 7/7 | 7 | 10,377 | 6,520 | 0.2149 |
| C1 (distilled-AIDAN) | **0.860** | 7/7 | 7 | 10,636 | 6,153 | 0.2070 |
| T (AIDAN staged) | **0.818** | 7/7 | 49 | 123,104 | 30,985 | 1.3901 |

Raw ordering this run: **C1 ≈ C0 > T**, and **T cost ≈ 6.7× C1** (49 staged calls vs 1). Reported as
a measurement only — see caveats before drawing any conclusion.

## Per-metric mean (weights = % of total; primary 75 + secondary 25)

| Metric | Weight | C0 | C1 | T | Method |
|---|---|---|---|---|---|
| P1 opportunity ranking | 15% | 0.857 | 0.964 | 0.821 | deterministic |
| P2 kill accuracy | 15% | 1.000 | 1.000 | 1.000 | deterministic |
| P3 critical-unknown id | 12% | 0.421 | 0.319 | 0.331 | **auto keyword-overlap heuristic — see caveats** |
| P4 experiment-discrimination quality | 10% | 0.810 | 0.818 | 0.768 | heuristic (needs human) |
| P5 capital efficiency | 11% | 1.000 | 1.000 | 1.000 | deterministic |
| P6 calibration & updating | 12% | 1.000 | 1.000 | 0.857 | deterministic (staged direction) |
| S1 evidence discipline | 10% | 1.000 | 1.000 | 1.000 | partial-auto |
| S2 decision usefulness | 8% | 1.000 | 1.000 | 1.000 | placeholder heuristic |
| S3 reasoning soundness | 7% | 0.500 | 0.500 | 0.500 | placeholder (fixed 0.5, needs human) |

## Per-case weighted score

| Case | C0 | C1 | T |
|---|---|---|---|
| dev-01 genuinely-attractive | 0.894 | 0.875 | 0.825 |
| dev-02 seductive-but-weak | 0.842 | 0.830 | 0.851 |
| dev-03 obviously-weak | 0.682 | 0.838 | 0.711 |
| dev-04 mixed-portfolio | 0.922 | 0.851 | 0.834 |
| dev-05 staged-update-down | 0.843 | 0.828 | 0.833 |
| dev-06 staged-update-up | 0.853 | 0.846 | 0.727 |
| dev-07 critical-unknown | 0.949 | 0.949 | 0.943 |

- **Best case, every arm:** dev-07 critical-unknown (≈0.94–0.95).
- **Worst:** C0 dev-03 (0.682), C1 dev-05 (0.828), T dev-06 (0.727).
- Notable: **P2 kill accuracy is perfect (1.0) for all arms** — every arm killed exactly the
  fatally-flawed opportunities and spared the sound ones. Capital efficiency (P5) also perfect.
- T's two weakest cases are the staged **update-up** (dev-06, 0.727) and **obviously-weak** (dev-03,
  0.711); its P6 (0.857) reflects one staged case where the staged process did not end in the
  ground-truth direction.

## Cost accounting (approximate)

Cost is estimated from the harness's **APPROX** price table (`common.py`, placeholder 5/25 USD per
Mtok), **not** actual Anthropic billing — treat as indicative.

| Item | Cost (USD, approx) |
|---|---|
| Probe (1 tiny validation call) | 0.0002 |
| Run 1 — ABORTED (transient DNS failures, ~half the cases failed) | 0.7810 |
| Run 2 — accepted clean run (this summary) | 1.8120 |
| **Total this session** | **≈ 2.59** |

## Anomalies & caveats

1. **Run 1 failed on transient DNS** (`getaddrinfo failed`) partway through — cases dev-04…dev-07
   never reached the API, so that run's numbers were invalid and are discarded. Fix: added bounded
   retry + exponential backoff to the live client (transient/DNS/timeout/429/5xx only; deterministic
   4xx not retried). Run 2 completed 7/7 for all arms.
2. **P3 (critical-unknown) is a keyword-overlap heuristic** and scores low across all arms
   (0.42/0.32/0.33). This very likely under-credits semantically-correct answers phrased differently
   from the ground-truth string. It must be replaced by (or tie-broken with) human judgement before
   it is load-bearing — it currently depresses every arm and could shift the ordering.
3. **S3 is a fixed 0.5 placeholder** (no human rater yet); S2/P4 are heuristics. The three
   human-judged metrics (P4/S2/S3 + P3 tie-break) are not yet real.
4. **Cost figures are approximate** (placeholder price table). Update `_APPROX_PRICE_PER_MTOK` in
   `common.py` with real pricing before relying on the dollar numbers or the cost-adjusted comparison.
5. This is **one** run of **7** cases with several not-yet-human-scored metrics — far too little to
   conclude anything about whether the AIDAN structure helps. Interpretation is deferred.
