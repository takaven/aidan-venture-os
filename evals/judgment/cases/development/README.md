# Development cases (inspectable)

**7 synthetic development cases** used to build, debug, and calibrate the harness and rubric. Freely
inspectable. The **holdout** (`../holdout/`) stays sealed and empty until the harness and thresholds
are frozen. See `../../README.md` for the frozen design and `../../rubrics/primary.md` for scoring.

## Schema (one JSON file per case)

| Field | Audience | Meaning |
|---|---|---|
| `case_id`, `title`, `archetype` | meta | identity + archetype |
| `capital_ceiling_usd` | **model-facing** | hard budget cap the decision must respect |
| `mandate` | **model-facing** | the decision being made |
| `staged` | meta | `false` = single-shot `evidence.items[]`; `true` = rounds `evidence.rounds.{T0,T1,T2}` (deliberately contradictory) |
| `opportunities[]` | **model-facing** | `{id, name, summary, ask_usd}` |
| `evidence` | **model-facing** | `items[]` (single-shot) **or** `rounds` (staged) |
| `hidden_ground_truth` | **SCORER-ONLY** | `strongest_opportunity`, `ranking`, `fund_hold_kill`, `kills`, `critical_unknown`, `good_cheap_test`, (staged) `expected_update_direction`, `rationale`, `notes` |

> **Runner contract:** the runner MUST send only the model-facing fields (`mandate`,
> `capital_ceiling_usd`, `opportunities`, `evidence`) to the model and MUST exclude
> `hidden_ground_truth` from every prompt. The ground truth is the eval author's key for deterministic
> scoring, not information the arms may see. Synthetic names only — no real companies.

## The cases

| File | Archetype | Staged | Tests |
|---|---|---|---|
| `dev-01-genuinely-attractive.json` | genuinely-attractive | no | pick the clear winner; kill a no-wedge and a no-liquidity foil |
| `dev-02-seductive-but-weak.json` | seductive-but-weak | no | resist narrative/TAM capture; kill on retention + willingness-to-pay |
| `dev-03-obviously-weak.json` | obviously-weak | no | fast cheap double-kill; preserve capital (allocate ~$0) |
| `dev-04-mixed-portfolio.json` | mixed-portfolio | no | ranking + kill accuracy + capital efficiency together, with distinct kill reasons |
| `dev-05-staged-update-down.json` | seductive-but-weak | **yes** | correct **downward** update: T0 winning signal revealed as an attribution artifact by T2 |
| `dev-06-staged-update-up.json` | genuinely-attractive | **yes** | correct **upward** update: lukewarm T0 → genuinely strong regulated pain by T2 (don't kill prematurely) |
| `dev-07-critical-unknown.json` | critical-unknown | no | identify the one gating unknown; **HOLD** + cheapest discriminating test rather than fund/kill |

Coverage: genuinely-attractive, seductive-but-weak, obviously-weak, mixed-portfolio, and
critical-unknown archetypes; two staged contradictory-evidence cases (one updating down, one up);
every case states an explicit capital ceiling and a clearly-marked hidden ground truth.
