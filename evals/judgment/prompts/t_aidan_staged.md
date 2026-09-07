# Arm T — AIDAN staged decision process (treatment)

**Role in the experiment:** the treatment. The **full staged AIDAN sequence**, same model, same
frozen evidence pack, same final output schema as C0/C1. `treatment_runner.py` drives the stages in
order, threading each stage's output into the next as `{{PRIOR}}`. The differentiator vs C1 is the
**forced multi-step decomposition** (and its extra cost, which the simplicity kill test weighs).

Every stage is prefixed with this shared context block (runner substitutes model-facing fields only):

```
Mandate: {{MANDATE}}
Capital ceiling (hard cap): USD {{CAPITAL_CEILING_USD}}
Opportunities:
{{OPPORTUNITIES}}
Evidence pack (use ONLY this; if rounds T0/T1/T2 are present, later rounds are newer and may
contradict earlier ones — update accordingly):
{{EVIDENCE}}
```

Rules for all stages: reason only from the evidence pack; never invent facts; be concise; output only
the artifact each stage asks for.

---

## Stage 1 — Observations

From the evidence pack, list the concrete **observations** (things the pack actually states), each
tagged with the opportunity id(s) it bears on and, for staged cases, the round (T0/T1/T2) it came
from. Do **not** infer or evaluate yet. Output a bullet list.

## Stage 2 — Claims & contradictions

`{{PRIOR}}` = Stage 1 observations.
Turn observations into **claims** (interpretations), each labelled with its supporting observation(s)
and a strength (`strong` / `weak` / `unsupported`). Explicitly flag any **contradictions** — including
where a later round (T1/T2) overturns an earlier claim. Output a bullet list of claims + a
`contradictions` list.

## Stage 3 — Kill Case (independent, per opportunity)

`{{PRIOR}}` = Stages 1–2.
For **each** opportunity, before considering upside, state the single fact that would make it a
definite **no**, and whether the evidence already establishes it. Mark each opportunity
`kill-now` / `survives`. Kill seductive-but-weak opportunities here. Output a per-opportunity list.

## Stage 4 — Critical assumptions

`{{PRIOR}}` = Stages 1–3.
For each opportunity that survived the Kill Case, list the **critical assumptions** it depends on
(the ones that, if false, sink it). Output a per-opportunity list.

## Stage 5 — Critical unknown

`{{PRIOR}}` = Stages 1–4.
Name the **single most decision-relevant unknown** across the surviving set — the one whose
resolution would most change the capital decision. One sentence + why it dominates.

## Stage 6 — Cheapest discriminating test

`{{PRIOR}}` = Stages 1–5.
Design the **cheapest, fastest experiment** that would discriminate the Stage-5 critical unknown.
State: description, estimated cost (USD, must fit within the ceiling), what it resolves, and the
decision if it passes vs fails. It must genuinely change the decision, not just gather color.

## Stage 7 — Capital ranking & final decision (STRICT output)

`{{PRIOR}}` = Stages 1–6.
Rank the opportunities, decide **Fund / Hold / Kill** for each with calibrated confidence, allocate
the capital ceiling (hold reserve rather than over-commit; total ≤ ceiling), and give an overall
recommendation with explicit confidence. Confidence must track evidence strength, not narrative
appeal.

Return **only** a single JSON object, no prose outside it, matching exactly the shared schema:

```json
{
  "ranking": ["<opp_id>", "..."],
  "opportunities": [
    { "id": "<opp_id>", "decision": "FUND | HOLD | KILL", "confidence": 0.0,
      "rationale": "one line", "kill_reason": "specific fatal flaw, or null",
      "critical_assumptions": ["..."] }
  ],
  "critical_unknown": "the single unknown the decision most hinges on",
  "cheapest_discriminating_test": {
    "description": "...", "est_cost_usd": 0, "what_it_resolves": "...",
    "decision_if_pass": "...", "decision_if_fail": "..."
  },
  "capital_allocation": [ { "id": "<opp_id>", "allocate_usd": 0 } ],
  "total_allocated_usd": 0,
  "overall_recommendation": "...",
  "confidence": 0.0
}
```

> The final scored artifact is the Stage-7 JSON. Intermediate stage outputs are retained by the runner
> for cost accounting and for qualitative inspection, but only Stage 7 is scored against the rubric.
