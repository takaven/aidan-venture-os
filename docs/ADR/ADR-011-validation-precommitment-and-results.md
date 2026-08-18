# ADR-011 — Validation Precommitment & Results

**Status:** Accepted
**Gate:** 3 — Validation & Investment (Slice 1)

## Context

Gate 3 moves from research uncertainty toward disciplined validation and
investment decisions. Slice 1 establishes the validation substrate only:
hypotheses, precommitted immutable test definitions, and observed results. No
next-action selection, investment decision, or BUILD logic is implemented. This
ADR records the durable decisions; it does not reopen the frozen architecture.

## Decisions

### Validation test is a precommitted, immutable definition — not an execution state machine

A `validation_test` fixes *what* is tested and *how success/kill are judged*
before any result exists. Its definition (criteria, method, spend/time ceilings,
structured metrics) is immutable at the database level (guard trigger); to change
a test you create a new one. It carries **no** execution status
(DEFINED/EXECUTING/COMPLETE…). Execution authority remains Gate 1:
`ActionRequest → Policy → Approval → run_status → execution result → Proof
Receipt`. A `validation_test` may hold a **set-once** `action_request_id`
provenance link (NULL→value once, same venture, enforced by composite FK); the
link neither reserves capital, approves, executes, nor implies success.

### Outcome is derived deterministically, never asserted

A `validation_result`'s outcome (PASS/FAIL/INCONCLUSIVE) is derived by a bounded
deterministic evaluator from the test's precommitted structured criteria and the
observed measurement — kill criterion outranks success; absent/subjective
criteria yield INCONCLUSIVE (never auto-PASS). No caller supplies the outcome and
no model self-certifies it. Observed measurement is stored separately from
interpretation; interpretation is not evidence and cannot change the outcome (a
recorded FAIL stays FAIL despite optimistic prose).

### Anti-hindsight precommitment

Success criterion, kill criterion and evidence requirement are fixed at test
creation and cannot be rewritten after results exist (immutability guard). This
prevents moving the goalposts once outcomes are known.

### Results append; contradictions coexist

`validation_result` is append-only. Multiple results per test are allowed; a
later PASS never deletes an earlier FAIL (or vice versa). There is no mutable
`final_result` winner — later next-action/investment reasoning must see all
results.

### Typed evidence domains, no global score

WTP evidence modality (`STATED_INTEREST < STATED_WILLINGNESS < SIGNED_COMMITMENT
< ACTUAL_PAYMENT`, plus `NOT_APPLICABLE`) and acquisition/usage measurement
(`OUTREACH_RESPONSE`, `LANDING_CONVERSION`, `ACQUISITION_COST`, `ACTIVATION`,
`RETENTION`, `USAGE`, `OTHER_MEASURED_METRIC`) are **separate** categorical
domains. There is no cross-domain numeric evidence-strength score, and no
universal `SIGNED_COMMITMENT ⇒ BUILD-ready` rule — sufficiency is a later,
context-specific decision.

### Result ≠ decision; no authority

A PASS result is not an investment decision, lifecycle advance, ActionRequest
success or programme success; a FAIL does not auto-KILL. Slice 1 validation APIs
have zero authority to mutate Mandate, Policy, kill switch, autonomy, budget,
approvals, ActionRequest status, Proof Receipts, investment decisions or
lifecycle. Proof Receipts (Gate 1) verify execution/result conditions, never
market truth.

### Reuse, no duplication

Hypotheses/tests/results link back to Gate 2 `opportunity`/`assumption`/
`observation` (and through them to Source Receipts) with DB-enforced venture
agreement. No second capital ledger, decision store, evidence system or workflow
engine is introduced. No dependency was added (stdlib + PostgreSQL).

## Consequences

- Slice 2 adds highest-value next-action selection over this validation state;
  Slice 3 adds governed investment decisions and capital.
- Migrations `0001–0008` remain unchanged; `0009` adds only Slice 1 schema.
