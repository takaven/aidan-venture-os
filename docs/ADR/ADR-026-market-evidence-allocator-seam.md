# ADR-026 — Market-Evidence ↔ Allocator Seam

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 1)

## Context

Through Gate 7, market observations/interpretations/evidence bundles terminated inside
`market/`; the existing deterministic allocator (`nextaction.recommend` →
`commitment.commit_recommendation` → `investment_decision_record` → `action_request`) read only
`validation_result`. So real market evidence could exist but could not become an attributable
next decision/action. Gate 8 Slice 1 connects the two — a connective extension, not a new
orchestrator.

## Decisions

### Gate-7 evidence becomes allocator input; existing chain reused

The existing recommender and commitment path are reused unchanged in shape. No new allocator,
orchestrator, workflow engine, `closed_loop_run`, or second decision/recommendation system is
introduced. The only additions are the seam itself.

### `MARKET` recommendation ↔ existing `MARKET` decision

`next_action_recommendation.action_type` is extended with `MARKET` (mirroring the existing
`investment_decision` enum value `MARKET`, not a new generic label). `MARKET` means **"run
another bounded governed market-validation action"** — it does NOT mean market success, demand,
willingness-to-pay, scale, or revenue. A `MARKET` recommendation commits (via the existing
`commit_recommendation`) to a `MARKET` `investment_decision_record` whose `resulting_action_id`
is a canonical Gate-7 `send_outreach` ActionRequest. The exact content/audience/offer/spend is
frozen later by `market_action_spec`, never invented inside the decision.

### Deterministic, criterion-reusing selection — no event→decision heuristics

The market-aware branch fires only when the classic allocator has no higher-value action
(`NO_HIGH_VALUE_ACTION_NOW`) AND the venture is OPERATING AND its opportunity has executed
market action(s) whose precommitted `validation_test` remains UNRESOLVED (no `validation_result`
yet) AND canonical market observations exist. In that case the highest-value next action is
another bounded market test (`MARKET`). Raw observations (REPLIED/BOUNCED/UNSUBSCRIBE/…) never
by themselves resolve the precommitted criterion — that remains Gate-3's arbiter — so there is
no `REPLIED→CONTINUE` / `BOUNCED→KILL` heuristic and no market score. A precommitted FAIL still
yields `KILL` through the classic path (Gate-3 doctrine outranks market activity); absent market
evidence, behaviour is unchanged. Uncertainty is preserved, not fabricated into a decision.

### Observations are evidence; interpretations are not consumed here

The allocator consumes market **observations** only. `recommendation_market_observation`
records the exact canonical observations a recommendation used (relational provenance, composite
FKs enforcing single-venture citation, append-only), mirroring `recommendation_validation_result`.
No `recommendation_market_interpretation` table is added — the Slice-1 allocator consumes no
interpretations, so no speculative/unused schema is created. Observations enter the
recommendation `input_hash` (sorted, canonical), so a new market observation changes the
recommendation's input identity and a stale market-aware recommendation cannot be silently
replayed or committed.

### Authority unchanged

Creating a recommendation writes only the recommendation + its provenance. Committing reuses the
existing governed path (decision + optional ActionRequest + Gate-1 policy evaluation). The seam
never executes a WorkerAdapter, writes a Proof Receipt, creates or mutates a `market_observation`
/ `market_interpretation` / `validation_result`, transitions lifecycle, or moves capital beyond
the existing bounded ActionRequest/Policy machinery. Cross-venture evidence citation is rejected
(composite FKs). No real provider, network, credential, capability, autonomy classification, or
observation window is added — those remain later Gate-8 slices; the first real run is not
performed here.

## Consequences

- Migration `0022` extends `next_action_recommendation.action_type` with `MARKET` and adds
  `recommendation_market_observation`; migrations `0001–0021` unchanged.
- `nextaction.py` gains a gated, deterministic market-aware fallback + observation provenance;
  `commitment.py` maps `MARKET → MARKET → send_outreach`. No new module, dependency, capability,
  or provider. The chain `market_observation → recommendation → investment_decision_record →
  resulting_action_id → ActionRequest` is now reconstructable — the basis for the real closed
  loop in later slices.
