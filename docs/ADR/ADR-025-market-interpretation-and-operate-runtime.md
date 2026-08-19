# ADR-025 — Market Interpretation & Operate Runtime

**Status:** Accepted
**Gate:** 7 — Market Runtime (Slice 3)

## Context

Slice 2 froze the canonical separation `SOURCE → OBSERVATION` (externally-attributable
evidence) and the market-action Proof Receipt (the exact authorized action occurred). Slice 3
adds the next seam — `OBSERVATION → INTERPRETATION` — and a read-only Operate Runtime that
hands a future allocator (Gate 8) the evidence it needs to choose the highest-value next
action, without making that decision here.

## Decisions

### Observations are evidence; interpretations are not

A `market_interpretation` is a bounded, provenance-cited *reading* of one or more canonical
`market_observation` rows (e.g. "replies contain repeated price objections"). It is explicitly
NOT evidence: the observations remain the only externally-attributable market evidence, and a
VERIFIED `MARKET_ACTION` Proof Receipt remains the only proof that the exact authorized action
occurred. A VERIFIED action proof never upgrades a later interpretation to "verified" evidence.

### Exact relational provenance, not a trusted id list

Every interpretation must cite ≥ 1 observation. Provenance is stored relationally in
`market_interpretation_source`, with composite FKs `(interpretation_id, venture_id)` and
`(observation_id, venture_id)` that DB-enforce single-venture provenance — a cross-venture
source mix cannot be inserted. Cited observations must also belong to the same market action
(action-scoped). The source set is de-duplicated and order-normalized before hashing.

### Kernel-derived hash; immutability; identity vs content

`interpretation_hash` is kernel-derived over `{venture, action spec, sorted (observation id,
evidence hash) pairs, interpreter kind/ref, type, payload}`; the caller-supplied hash is never
trusted, and no numeric "confidence" is treated as objective truth. `interpretation_key` is the
stable identity: re-using a key with identical content converges; a materially changed payload
or a changed source set conflicts. New evidence yields a NEW interpretation (a new key) — the
old interpretation is never mutated; history stays visible. Both tables are append-only
(UPDATE/DELETE guarded).

### Interpretation has no authority

An interpreter (deterministic kernel, model, or adapter) is advisory. Creating an
interpretation writes only the interpretation + its source rows. It cannot rewrite an
observation or Proof Receipt, create a `market_observation`, write an
`investment_decision_record`, transition lifecycle, move capital, create an ActionRequest, or
mutate a `validation_result`. A `recommended_lifecycle` / `SCALE` / `verified` field in an
interpreter payload — or a prompt-injection string in a cited observation — is inert DATA. The
Gate-3 historical `validation_result` remains immutable; market observations + interpretations
are new operating evidence, not a PASS/FAIL rewrite.

### Metrics are derived; no market score

Counts (`delivered/bounced/opened/clicked/replied/unsubscribe_count`) are computed by query
over canonical observations — no metrics table, and no scalar market/traction/demand score.
Slice-2 source-scoped dedupe means a duplicate external event never inflates a count. Rate
discipline: a rate is returned only when its denominator exists canonically; no eligible-send /
recipient-population denominator is frozen anywhere, so `reply_rate` is UNAVAILABLE (`None`) —
never fabricated from an arbitrary action/attempt count.

### `NO_RESPONSE` stays deferred

No canonical observation-window / deadline primitive exists (0019 `market_action_spec` has none;
`market_observation.occurred_at` is a per-event timestamp, not a frozen window). Absence
therefore cannot be established deterministically, so `NO_RESPONSE` is neither ingested nor
derived in Slice 3, and no window was added to satisfy a test. A future exit criterion needing
deterministic absence must first add the missing window primitive.

### Operate Runtime supplies allocator-ready evidence, not decisions

`market_evidence_bundle(...)` reconstructs, by pure query, one market action's frozen spec +
Gate-2/3 provenance, its action Proof Receipt (kept distinct), canonical observations
(contradictory evidence retained), deterministic counts, and interpretations (kept distinct,
with exact source provenance). It contains no CONTINUE/KILL/SCALE decision and no next-action
recommendation, and writes nothing. Repeated market cycles reuse the existing ActionRequest /
spec primitives — each action has its own spec and its own bundle — so earlier history is never
overwritten and no `operate_run` workflow engine is introduced. Gate 8 owns the closed loop.

## Consequences

- Migration `0021` adds `market_interpretation` + `market_interpretation_source` and an additive
  `UNIQUE(id, venture_id)` on `market_observation`; migrations `0001–0020` unchanged.
- New production modules `aidan_core/market/{interpretation,metrics,operate}.py`. No new
  capability (`SEND_OUTREACH` remains the only Gate-7 worker capability), no market score, no
  decision/lifecycle/budget authority, no real send, no network/provider, no dependencies.
