# Development evaluation — live run summary

Run: 2026-09-07T10:22:00Z · model: `claude-opus-4-8` · development cases: 7 · arms: C0, C1, T.

This records observed development-run output only. No PASS/AMBIGUOUS/FAIL decision or numeric
threshold is applied here.

## Aggregate results

| Arm | Mean weighted score | P1 | P2 | P3 | P4 | P5 | P6 | Input tokens | Output tokens | Calls | Estimated cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 0.8551 | 0.8571 | 1.0000 | 0.4207 | 0.8100 | 1.0000 | 1.0000 | 10,377 | 6,520 | 7 | 0.214885 |
| C1 | 0.8597 | 0.9643 | 1.0000 | 0.3189 | 0.8178 | 1.0000 | 1.0000 | 10,636 | 6,153 | 7 | 0.207005 |
| T | 0.8176 | 0.8214 | 1.0000 | 0.3307 | 0.7685 | 1.0000 | 0.8571 | 123,104 | 30,985 | 49 | 1.390145 |

Total estimated cost: **$1.812035**. Costs are the harness's approximate usage-based estimates.

## Case highs and lows

| Arm | Highest weighted-score case | Score | Lowest weighted-score case | Score |
|---|---|---:|---|---:|
| C0 | `dev-07-critical-unknown` | 0.9493 | `dev-03-obviously-weak` | 0.6821 |
| C1 | `dev-07-critical-unknown` | 0.9489 | `dev-05-staged-update-down` | 0.8283 |
| T | `dev-07-critical-unknown` | 0.9430 | `dev-03-obviously-weak` | 0.7105 |

## Execution notes

- All 21 arm×case records were marked OK; no parse failures were recorded.
- T made 49 calls because the staged treatment executes sequential rounds; C0 and C1 made seven calls each.
- The complete machine-readable score record and rendered report remain local and ignored at
  `evals/judgment/results/scored.json` and `evals/judgment/results/report.md`.
