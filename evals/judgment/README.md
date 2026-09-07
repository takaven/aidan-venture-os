# AI-DAN Phase-1 Judgment Evaluation Harness

> **Status:** design frozen (this document, the rubric, the arm prompts, and the development-case
> schema). Scoring **logic** and the numeric PASS thresholds are still to be implemented/frozen — see
> the `TODO`s in `scorer/score.py` and `rubrics/primary.md`. This is the **only currently allowed
> work** on the programme (strategic pivot, 2026-09-07 — `docs/PROGRAMME_STATUS.md` §0).

## What is being tested

> Does the **same frontier model**, routed through AIDAN's structured decision process, produce
> **materially better, cost-adjusted** venture decisions than (1) a strong general prompt and (2) a
> much simpler distilled-AIDAN prompt?

It is a **judgment** test, not a plumbing test. No live research, no providers, no deployment, no
market send (all parked). Every arm sees the **same fixed, frozen evidence pack** for a case and must
decide from that alone. The harness is designed to be able to **kill the structure**, not just
validate it.

## The three arms (same model, same evidence, same output schema)

| Arm | Name | Prompt | What it is |
|---|---|---|---|
| **C0** | General control | `prompts/c0_general.md` | One strong, generic venture-investment prompt. No AIDAN structure or vocabulary. |
| **C1** | Distilled-AIDAN control | `prompts/c1_distilled_aidan.md` | One compact prompt carrying AIDAN's *doctrine* (evidence vs interpretation, independent Kill Case, critical unknown, cheapest discriminating test, capital ranking) — but **no** multi-stage orchestration or ledgers. |
| **T** | AIDAN staged treatment | `prompts/t_aidan_staged.md` | The full staged AIDAN sequence: observations → claims/contradictions → Kill Case → assumptions → critical unknown → cheapest discriminating test → capital ranking under budget → Fund/Hold/Kill with confidence. |

**C1 is the pivotal control** and the simplicity kill test: if C1 (cheap, one pass) captures nearly
all of T's benefit, the staged structure is not earning its complexity.

## Scoring philosophy

- **Objective metrics dominate (≈70–80%).** They are scored against each case's sealed
  `hidden_ground_truth`, not against prose polish or document completeness. See `rubrics/primary.md`.
- **Secondary metrics are a minority (≈20–30%)** and cover qualities a key cannot fully capture.
- **Cost-adjusted.** Runners record tokens/cost per case; the headline comparison is **quality per
  dollar**, so an arm that wins on quality but costs far more can still fail the bar.
- **Blinded & pre-registered.** The harness/rubric are built and calibrated on the **development**
  cases only; the **holdout** stays sealed and is scored **once** after thresholds are frozen. No
  post-hoc threshold tuning.

Primary metric families (defined in `rubrics/primary.md`): opportunity ranking, kill accuracy,
critical-unknown identification, experiment-discrimination quality, capital efficiency, and
calibration/updating.

## Precommitted decision tree (PASS / AMBIGUOUS / FAIL)

> The **shape** is frozen here. The **numbers** (material-lift margin, cost-adjustment formula) are a
> required pre-registration step committed **before** the holdout is unsealed — `rubrics/primary.md`.

Evaluated on the **holdout**, cost-adjusted:

- **PASS** — T beats **both** C0 and C1 by a material cost-adjusted margin. The structure is justified;
  proceed toward the thin composed venture loop.
- **AMBIGUOUS** — T beats C0 but **not** C1 materially, or results are within noise. Do not declare
  victory; investigate which stages of T add value and prefer the cheapest arm that keeps the benefit.
- **FAIL / SIMPLIFY** — C1 (or even C0) captures nearly all of T's benefit, or T is not better
  cost-adjusted. **The staged structure is not justified as-is; simplify it** to the smallest arm that
  retains the benefit.

**Simplicity kill test (primary kill rule):** if C1 ≈ T on cost-adjusted quality, the structure is
killed/simplified regardless of whether T beats C0 — a cheap win beats expensive ceremony
(capital-scarcity doctrine, ADR-037).

## Development vs holdout

- `cases/development/` — **6–8** cases used to build, debug, and calibrate the harness and rubric.
  Freely inspectable. (Authored; see below.)
- `cases/holdout/` — **12–15 SEALED** cases scored once at the end. **Do not create or read holdout
  content while building the harness** (contamination kill). Currently empty by design.

## Case schema (development)

Each development case is one JSON file (`cases/development/dev-NN-*.json`) with a consistent schema.
The **model-facing** fields and the **scorer-only** ground truth are separated so the runner can strip
the latter before any model call:

| Field | Audience | Meaning |
|---|---|---|
| `case_id`, `title`, `archetype` | meta | identity + one of `genuinely-attractive` / `seductive-but-weak` / `obviously-weak` / `mixed-portfolio` / `critical-unknown` |
| `capital_ceiling_usd` | **model-facing** | the fixed budget the decision must respect |
| `mandate` | **model-facing** | the investor mandate / decision being made |
| `staged` | meta | `false`, or `true` when evidence arrives in rounds `T0 → T1 → T2` |
| `evidence` | **model-facing** | either `items[]` (single-shot) or `rounds` keyed `T0/T1/T2` (staged, deliberately contradictory) |
| `opportunities` | **model-facing** | the candidate ventures `{id, name, summary, ask_usd}` |
| `hidden_ground_truth` | **scorer-only** | strongest opportunity, full ranking, fatal flaws / kills, the correct critical unknown, a good cheap discriminating test, per-opportunity Fund/Hold/Kill, and rationale. **The runner MUST exclude this key from anything sent to the model.** |

Names are synthetic (no real companies a model is likely to have memorised). `hidden_ground_truth` is
the eval author's key, not a claim of objective certainty; it encodes the intended-correct call so
scoring is deterministic where possible.

## Layout

```
evals/judgment/
├── README.md                 # this file — frozen design
├── cases/
│   ├── development/          # 6–8 dev cases (authored; inspectable)
│   └── holdout/              # 12–15 SEALED cases (empty by design; do NOT create/peek yet)
├── rubrics/
│   └── primary.md            # concrete metrics + weights + threshold placeholder
├── prompts/
│   ├── c0_general.md         # arm C0
│   ├── c1_distilled_aidan.md # arm C1 (simplicity kill test)
│   └── t_aidan_staged.md     # arm T (staged)
├── runners/
│   ├── control_runner.py     # runs C0 and C1                [stub — NotImplementedError]
│   └── treatment_runner.py   # runs T (staged)               [stub — NotImplementedError]
└── scorer/
    └── score.py              # rubric + decision tree + cost adjustment  [stub — NotImplementedError]
```

## Non-goals (Phase 1)

- No live research, providers, deployment, or market send (all parked).
- No claim about profitable venture creation — only about *decision quality* on fixed evidence.
- No changes to runtime/product code, workflows, migrations, or kernel tests.
