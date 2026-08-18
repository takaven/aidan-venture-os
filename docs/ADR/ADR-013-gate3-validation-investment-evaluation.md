# ADR-013 — Gate 3 Validation → Investment Evaluation

**Status:** Accepted
**Gate:** 3 — Validation & Investment (Slice 3 governance + Slice 4 evaluation)

## Context

Gate 3 turns evidence-backed Gate 2 Opportunities and validation evidence into
disciplined, capital-efficient next actions. Slice 1 added the validation
precommitment substrate, Slice 2 the highest-value next-action allocator
(ADR-012). Slice 3 added the governed conversion of a recommendation into a
canonical investment decision and, when consequential, a Gate 1 ActionRequest;
Slice 4 adds the integrated end-to-end evaluation evidence. This ADR records the
integrated boundary — Slice 3's governance decisions were not previously captured
in an ADR — and the eval evidence's scope. It does not reopen the frozen
architecture.

## Decisions

### Governed commitment is the only recommendation → decision path

`commitment.commit_recommendation` is the sole path from a `next_action_recommendation`
to a canonical `investment_decision_record`. It enforces, in one transaction:
staleness rejection (the recommendation's basis is recomputed and must still
match), 1:1 compatibility (VALIDATE/BUILD/HOLD/KILL only; `RESEARCH_MORE` maps to
no decision and is refused), an independent BUILD re-gate, and the VALIDATE spend
bound (`≤ validation_test.max_spend`). One governed decision per recommendation
(DB-enforced), linked to its recommendation basis (`source_recommendation_id`).

### BUILD is re-gated, never trusted from the recommendation

A BUILD recommendation is re-verified against current canonical state before a
BUILD decision: Opportunity still CANDIDATE; a Claim with positive provenance
(Claim → SUPPORTS → Observation → Source Receipt); a complete Kill Case; no
unresolved CRITICAL assumption; a contextual WTP-context PASS and a contextual
acquisition-context PASS, each against that test's own precommitted criterion; no
decisive kill; no ignored PASS/INCONCLUSIVE contradiction. There is no universal
rule (no `SIGNED_COMMITMENT ⇒ BUILD`, no required `ACTUAL_PAYMENT`, no numeric WTP
threshold, no "structural completeness ⇒ BUILD"). Build-nothing is a valid,
frequent outcome.

### Consequential actions enter the existing Gate 1 policy boundary

A consequential VALIDATE/BUILD ActionRequest is submitted into the existing Gate 1
policy path (reusing `policy.current_evaluation`/`persist_decision`,
`approvals.create_pending`, `execution._set_status`) — a canonical
`policy_decision` (ALLOW / REQUIRE_APPROVAL / DENY) is evaluated from live
kill-switch/budget/autonomy state; REQUIRE_APPROVAL opens a PENDING approval and
moves the action to AWAITING_APPROVAL. No new Gate 3 policy/approval/budget engine
was introduced.

### Separation is load-bearing

Recommendation ≠ Investment Decision ≠ ActionRequest ≠ Policy authorization ≠
Execution ≠ Lifecycle state. Recording a decision moves no capital; creating an
ActionRequest is not spend; Policy ALLOW is not execution; approval PENDING is not
success. Gate 3 performs no execution: no `execution_attempt`, no `proof_receipt`,
no ActionRequest SUCCESS, no lifecycle transition. Governance denial (kill switch,
budget, autonomy) is a governance-plane fact and never fabricates a FAIL
validation result or alters market evidence.

### Evaluation evidence and its limits

Slice 4 adds a development eval suite and a separate held-out eval set, both
driving the same production functions from explicitly constructed canonical state
with no production special-casing and no injected expected output. These
deterministic/replay fixtures prove decision discipline and architecture only —
never commercial success, live demand, payment processing, autonomous build, or
Gate 4 worker execution. Held-out cases demonstrate the invariants generalize
beyond the development fixtures, not commercial validity.

## Consequences

- Migrations `0001–0011` remain unchanged; Slice 4 adds no migration, no
  production module, and no `investment_decision` enum change.
- Gate 3 remains open pending an independent exit audit; Gate 4 (durable worker
  execution) is out of scope. A Gate 3 ActionRequest may exist without execution —
  that is correct.
