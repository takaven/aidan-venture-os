# ADR-012 — Highest-Value Next Action

**Status:** Accepted
**Gate:** 3 — Validation & Investment (Slice 2)

## Context

The fundamental unit of the programme is the highest-value next action, not
BUILD. Slice 2 adds the allocator that selects the next action for an Opportunity
from current research/validation state. It makes no investment decision, spends
no capital, and executes nothing. This ADR records the durable decisions; it does
not reopen the frozen architecture.

## Decisions

### A recommendation is reasoning, not authorization

A `next_action_recommendation` (RESEARCH_MORE / VALIDATE / BUILD / HOLD / KILL) is
a reasoning artifact — not a canonical investment decision, ActionRequest,
approval, capital movement, Proof Receipt, opportunity-status change or lifecycle
transition. A `BUILD` recommendation means only "current evidence suggests BUILD
should be considered next"; Slice 3 independently enforces the canonical
investment/BUILD gate. `RESEARCH_MORE` is deliberately not added to the
`investment_decision` enum here.

### Categorical selection, no fake precision

Selection is a small set of explicit ordered rules producing an action plus a
finite `dominant_reason_code` and provenance. There is no expected-value /
information-gain decimal, no composite opportunity score, no weighted confidence,
and no synthetic value/cost ratio. Real structured data (assumption importance,
validation outcomes, `max_spend`, `max_duration_days`) is compared directly.

### Rule order

1. A precommitted **kill criterion triggered** (a deterministic `FAIL` result)
   outranks generated optimism → **KILL**.
2. An **unresolved CRITICAL/HIGH assumption** (resolved == has a `PASS` and no
   `INCONCLUSIVE`) with a discriminating test → **VALIDATE**; with no credible
   test → **RESEARCH_MORE**.
3. **Contradiction without a decisive kill** (`PASS` + `INCONCLUSIVE`) →
   **VALIDATE** if a discriminating test remains, else **HOLD**.
4. **BUILD consideration** only when the Opportunity is structurally complete
   (a CANDIDATE), there is genuine positive evidence (a `PASS`), and every
   CRITICAL/HIGH assumption is cleanly resolved.
5. Otherwise **HOLD**.

### cheapest_test is input, not command; tests are chosen deterministically

Gate 2's `cheapest_test` is descriptive reasoning only — never auto-executed.
Only canonical `validation_test` records are executable. Among tests eligible for
an assumption (linked via its hypothesis), only those with structured success
criteria are treated as *discriminating* (able to deterministically resolve it);
among those the allocator prefers lower real `max_spend`, then `max_duration_days`,
then a stable id. No new "discrimination" field and no score were introduced.

### Precommitted kill outranks optimism; contradictions preserved

Because a `FAIL` outcome arises only from a precommitted kill criterion, a `FAIL`
is decisive and cannot be overridden by an optimistic interpretation. Contradictory
validation results remain visible (append-only); the allocator never selects a
positive result while suppressing a negative one.

### WTP and acquisition stay separate; no universal threshold

WTP modality and acquisition/usage measurement remain distinct domains with no
cross-domain score. There is no universal `SIGNED_COMMITMENT ⇒ BUILD` rule:
sufficiency emerges only from each assumption's own precommitted validation test
passing. Strong WTP alone, or strong acquisition alone, never forces BUILD while
another critical assumption is unresolved.

### Append-only history with explicit basis

Recommendations are append-only; later evidence produces a new recommendation and
never rewrites a prior one. Each recommendation links the exact Assumptions,
Validation Tests and Validation Results it considered, so its basis is inspectable
historically rather than re-queried from current state. Idempotent per
`(venture, recommendation_key)`; the same key over a changed input state conflicts.

### No authority; no dependency

The allocator has zero authority over Mandate, Policy, kill switch, autonomy,
budget, approvals, ActionRequest status, Proof Receipts, investment decisions or
lifecycle. No dependency was added.

## Consequences

- Slice 3 integrates recommendation → governed investment decision + capital.
- Migrations `0001–0009` remain unchanged; `0010` adds only Slice 2 schema.
