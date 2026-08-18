# ADR-009 — Interpretations, Assumptions, Opportunities & Kill Cases

**Status:** Accepted
**Gate:** 2 — Autonomous Research (Slice 3)

## Context

Slice 3 extends the Gate 2 research model from evidence (SOURCE → OBSERVATION →
CLAIM) to reasoning artifacts (INTERPRETATION → ASSUMPTION → OPPORTUNITY →
KILL CASE). This ADR records the durable decisions; it does not reopen the frozen
architecture.

## Decisions

### Reasoning is not evidence

Interpretation, Assumption, Opportunity and Kill Case are **not** subtypes of
`evidence_record` and are not evidence. They are standalone typed tables that
link *back* to canonical Claims (which are evidence). No second evidence ledger
is created, and reasoning can never be persisted as an Observation or Claim.

### Interpretation cannot mutate Claim truth

An Interpretation reasons over Claims (SUPPORTED/CONTRADICTED/DISPUTED/
UNSUPPORTED) but never changes their structural state or their underlying
SUPPORTS/CONTRADICTS relations. A DISPUTED Claim stays DISPUTED after
interpretation. `produced_by` is provenance about who/what reasoned, not proof.

### Assumptions are categorical, with consequence and cheapest test

Importance (LOW/MEDIUM/HIGH/CRITICAL) and confidence (LOW/MEDIUM/HIGH) are
categorical — no decimals or percentages (rejected in code and by DB CHECK).
Every Assumption records `consequence_if_false` and a `cheapest_test`, which is a
plain research hypothesis only; Gate 3 selects, prices and funds experiments.

### Opportunity is a research candidate, not approval

An Opportunity links to Claims/Interpretations/Assumptions (never duplicating
evidence). Content is immutable (DB trigger); only its research status
transitions, through guarded operations — there is no arbitrary status setter.
Statuses: DRAFT, INSUFFICIENT_EVIDENCE, CANDIDATE, KILLED. Reaching CANDIDATE is
**structural Gate-2 readiness, not BUILD** and has no capital/investment/
ActionRequest/lifecycle side effect.

### Candidate finalization requires a complete Kill Case

`finalize_candidate` is guarded: it requires buyer/problem/critical-unknown
hypotheses, ≥1 linked Claim, ≥1 linked Assumption, and an adversarial Kill Case
with **all eleven required dimensions** assessed. It does **not** require Claims
to be SUPPORTED and imposes no numeric threshold — contradictory evidence stays
allowed and visible. A serious candidate cannot become CANDIDATE without the
Kill Case.

### Kill Case is adversarial reasoning, categorical, linked to evidence

Kill Case rationale is reasoning, not evidence. Dimension assessments are
categorical (LOW_RISK/MATERIAL_RISK/SEVERE_RISK/INSUFFICIENT_EVIDENCE — no
scores). Missing evidence is recorded as INSUFFICIENT_EVIDENCE, never LOW_RISK.
Dimensions may link to canonical Claims (whose state is preserved).

### Insufficient evidence and no credible opportunity are valid

INSUFFICIENT_EVIDENCE is a first-class opportunity status with a recorded reason,
inventing no evidence. Zero candidate opportunities is a valid success case; a
durable `research_result` may record NO_CREDIBLE_OPPORTUNITY /
INSUFFICIENT_EVIDENCE / OPPORTUNITIES_FOUND. Neither abuses `investment_decision`
— Gate 3 owns investment decisions.

### No authority; append-only reasoning; no dependency

Reasoning artifacts have zero authority over Mandate, Policy, kill switch,
autonomy, capital, approvals, ActionRequest success, Proof Receipts, venture
lifecycle or investment decisions. Interpretations, Assumptions, Kill Cases and
their links are append-only; only Opportunity status transitions (guarded,
audited). No dependency was added (stdlib-only). No autonomous orchestration or
LLM is implemented in Slice 3.

## Consequences

- Slice 4 adds the provider-neutral adapter loop and Gate 2 evaluation over this
  reasoning substrate.
- Migrations `0001–0006` remain unchanged; `0007` adds only Slice 3 schema.
