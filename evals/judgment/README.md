# AI-DAN Phase-1 Judgment Evaluation Harness

> **Status:** SKELETON. Design frozen (this document). Cases, prompts, runners, and scoring logic are
> **not yet implemented** — see the `TODO`s in each placeholder file. This is the **only currently
> allowed work** on the programme (strategic pivot, 2026-09-07 — see `docs/PROGRAMME_STATUS.md` §0).

## Purpose

Answer the programme's existential question **directly, offline, and cheaply**:

> Does the **same frontier model**, routed through AIDAN's structured decision process, produce
> **materially better, cost-adjusted** venture decisions than (1) a strong general prompt and (2) a
> much simpler distilled-AIDAN prompt?

If AIDAN's structure does **not** add material lift over a simple distilled prompt, the structure is
**not justified** and must be simplified. This harness is designed to be capable of **killing the
structure**, not just validating it.

This is a **judgment** test, not a plumbing test. It deliberately uses **no** live research, no
providers, no deployment, no market send — those are all parked. It isolates *decision quality* from
*execution capability*.

## The three arms (same model, same evidence)

All arms are run with the **same frontier model** on the **same fixed frozen evidence packs**. The
only thing that varies is the prompt/process wrapped around the model.

| Arm | Name | Prompt file | Description |
|---|---|---|---|
| **C0** | Control — general | `prompts/c0_general.md` | A strong, generic "you are an expert venture analyst; here is the evidence; make the call" prompt. No AIDAN structure. |
| **C1** | Control — distilled AIDAN | `prompts/c1_distilled_aidan.md` | A single compact prompt that distills AIDAN's *doctrine* (highest-value next action, Kill Case, capital scarcity, evidence discipline) into one pass — **but not** the staged multi-step process. |
| **T** | Treatment — AIDAN staged | `prompts/t_aidan_staged.md` | The full AIDAN structured decision process (staged: evidence intake → observations/claims → interpretation/assumptions → Kill Case → capital decision → next-best-action), same model, driven by the treatment runner. |

C1 is the pivotal control: it is the **simplicity kill test**. If C1 (cheap, one pass) captures nearly
all of T's benefit, the staged structure is not earning its complexity.

## Experimental design — FROZEN RULES

These rules are frozen. Changing any of them invalidates a comparison and requires re-running.

1. **Same model for all arms.** C0, C1, and T use the identical model and identical decoding
   parameters. Any model change re-baselines all arms.
2. **Fixed frozen evidence packs — no live research.** Each case ships a sealed, versioned evidence
   pack. Arms may use **only** the evidence in the pack. No arm may call a live research provider, the
   web, or any external system. (This isolates judgment from retrieval.)
3. **Three arms: C0, C1, T** — as defined above.
4. **Development / holdout split.**
   - `cases/development/` — **6–8** cases used to build and debug the harness, iterate rubrics, and
     sanity-check the arms. Freely inspectable.
   - `cases/holdout/` — **12–15 sealed** cases. **Do not look at holdout case contents while building
     the harness.** The holdout is scored **once**, after the harness and rubric are frozen on the
     development set. Peeking at the holdout invalidates it.
5. **Objective metrics dominant.** The rubric (`rubrics/primary.md`) weights **objective/verifiable**
   metrics at **70–80%** and secondary/qualitative metrics at **20–30%**. Scoring is against a
   per-case answer key (correct decision, correct kill/no-kill, correct cheapest-decisive action,
   capital within scarcity frame), not against prose polish or document completeness.
6. **Precommitted decision tree (PASS / AMBIGUOUS / FAIL).** The thresholds and the tree are frozen
   *before* the holdout is scored (see below). No post-hoc threshold tuning.
7. **Explicit simplicity kill test.** If **C1 captures nearly all of T's benefit** (T's cost-adjusted
   advantage over C1 is below the material-lift threshold), the outcome is **simplify the structure** —
   regardless of whether T beats C0.
8. **Cost tracking required.** Every arm records tokens/cost per case. The headline comparison is
   **cost-adjusted** decision quality (quality per dollar), not raw quality. A structure that wins on
   quality but costs far more may still fail the cost-adjusted bar.

## Success criteria & precommitted decision tree

> **TODO (before scoring the holdout):** fill in the exact numeric thresholds (material-lift margin,
> cost-adjustment formula, per-metric weights). They must be committed to the repo **before** the
> holdout is unsealed. The *shape* of the tree is frozen here; the *numbers* are a required
> pre-registration step.

Evaluated on the **holdout**, cost-adjusted, with thresholds pre-registered:

- **PASS** — T beats **both** C0 and C1 by a **material** cost-adjusted margin. AIDAN's structure is
  justified; proceed toward the thin composed venture loop.
- **AMBIGUOUS** — T beats C0 but **not** C1 by a material margin, or results are within noise. Do not
  declare victory; investigate which stages of T add value, and prefer the cheapest arm that captures
  the benefit.
- **FAIL / SIMPLIFY** — C1 (or even C0) captures nearly all of T's benefit, or T's cost-adjusted
  result is not better. **The staged structure is not justified as-is; simplify it** to the smallest
  arm that retains the benefit.

## Kill rules

- **Structure kill (primary):** if the simplicity kill test fires (C1 ≈ T on cost-adjusted quality),
  the staged structure is killed/simplified. This is a first-class successful outcome, consistent with
  the capital-scarcity doctrine (ADR-037) — cheap wins beat expensive ceremony.
- **Harness kill:** if the development set cannot produce a rubric that discriminates a deliberately
  strong answer from a deliberately weak one, the harness itself is invalid and must be fixed before
  any holdout run.
- **Contamination kill:** if the holdout is inspected during development, or evidence packs are found
  to leak the answer key, affected cases are void.

## Layout

```
evals/judgment/
├── README.md                 # this file — frozen design
├── cases/
│   ├── development/          # 6–8 dev cases (inspectable)   [TODO: author cases]
│   └── holdout/              # 12–15 sealed cases            [TODO: author + seal; do NOT peek]
├── rubrics/
│   └── primary.md            # objective (70–80%) + secondary (20–30%) metrics  [TODO]
├── prompts/
│   ├── c0_general.md         # arm C0 prompt                 [TODO]
│   ├── c1_distilled_aidan.md # arm C1 prompt (simplicity kill test)  [TODO]
│   └── t_aidan_staged.md     # arm T staged process          [TODO]
├── runners/
│   ├── control_runner.py     # runs C0 and C1                [TODO — stub only]
│   └── treatment_runner.py   # runs T (staged)               [TODO — stub only]
└── scorer/
    └── score.py              # applies rubric + decision tree + cost adjustment  [TODO — stub only]
```

## Non-goals (explicitly out of scope for Phase 1)

- No live research, providers, deployment, or market send (all parked).
- No claim about profitable venture creation — only about *decision quality* on fixed evidence.
- No changes to runtime/product code, workflows, migrations, or kernel tests.
