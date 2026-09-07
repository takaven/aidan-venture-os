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

## Running the development evaluation

The harness runs all three arms (C0, C1, T) over the 7 development cases, scores each output against
the case's `hidden_ground_truth`, writes results to `results/` (gitignored), and prints a
C0 vs C1 vs T comparison. It is **stdlib-only** and does not import the production package.

**Default is `mode=mock`: zero cost, no network** — it exercises the full pipeline (load → strip
ground truth → prompt → parse → score → report) using a naive baseline "model", so you can validate
the plumbing and the scorer without spending anything.

```bash
# free mock smoke of the whole pipeline (no API key, no network, no cost)
python evals/judgment/run_dev.py
```

Outputs (under `evals/judgment/results/`, gitignored):
- `raw/<case_id>__<arm>.json` — each arm's raw output + parsed decision + token/cost.
- `scored.json` — per-case, per-arm, and aggregate scores (machine-readable).
- `report.md` — the C0 vs C1 vs T summary table + per-metric means.

**Live (paid) runs are explicit opt-in only** — the same single model is used for every arm.

**Where to put the API key (recommended: a gitignored local file).** `common.py` loads `.env` then
`.env.local` from this directory into the environment before reading its defaults; both are gitignored
and never committed. A real environment variable always wins over the files.

```bash
# one-time: create your local secrets file from the template, then edit it
cp evals/judgment/.env.local.example evals/judgment/.env.local
# put ANTHROPIC_API_KEY=sk-ant-... in it (an Anthropic console API key — pay-per-token,
# separate from any Claude.ai subscription). Optionally set AIDAN_EVAL_MODE=live there too.
```

Then run live (either mode via flag, or set `AIDAN_EVAL_MODE=live` in `.env.local`):

```bash
# ONLY when you intend to spend. Same model for all arms; cost is read from API usage.
python evals/judgment/run_dev.py --mode live --model claude-opus-4-8

# equivalently, without a file, via inline env vars:
AIDAN_EVAL_MODE=live ANTHROPIC_API_KEY=sk-... python evals/judgment/run_dev.py --mode live
```

Flags: `--mode {mock,live}` · `--model <id>` (default `claude-opus-4-8`, override via `AIDAN_EVAL_MODEL`)
· `--arms C0,C1,T` · `--out <dir>`. The model id is a single swappable constant so every arm uses the
same model (frozen-design requirement). Cost figures in live mode use an **approximate** price table
in `common.py` — verify pricing before relying on the dollar numbers.

> Scoring today applies **no** PASS/AMBIGUOUS/FAIL thresholds. After a real (live) development run we
> review the numbers, then pre-register the thresholds (`rubrics/primary.md`) **before** any holdout
> work. Deterministic metrics (P1/P2/P5, staged P6, part of S1) are computed directly; P3/P4/S2/S3 use
> transparent heuristics flagged for human raters.

## Layout

```
evals/judgment/
├── README.md                 # this file — frozen design + run instructions
├── run_dev.py                # top-level: run all arms × all dev cases, score, write results/
├── common.py                 # shared: case loading, prompt rendering, model client, JSON parse, cost
├── .env.local.example        # copy to .env.local (gitignored) and add your ANTHROPIC_API_KEY
├── .gitignore                # ignores results/, __pycache__, .env, .env.local
├── cases/
│   ├── development/          # 7 dev cases (authored; inspectable)
│   └── holdout/              # 12–15 SEALED cases (empty by design; do NOT create/peek yet)
├── rubrics/
│   └── primary.md            # concrete metrics + weights (sum to 100) + threshold placeholder
├── prompts/
│   ├── c0_general.md         # arm C0
│   ├── c1_distilled_aidan.md # arm C1 (simplicity kill test)
│   └── t_aidan_staged.md     # arm T (staged)
├── runners/
│   ├── control_runner.py     # runs C0 and C1 (one call each)
│   └── treatment_runner.py   # runs T (staged, sequential calls)
├── scorer/
│   └── score.py              # rubric metrics + per-case/arm/aggregate scoring (no thresholds yet)
└── results/                  # (gitignored) raw/<case>__<arm>.json, scored.json, report.md
```

## Non-goals (Phase 1)

- No live research, providers, deployment, or market send (all parked).
- No claim about profitable venture creation — only about *decision quality* on fixed evidence.
- No changes to runtime/product code, workflows, migrations, or kernel tests.
