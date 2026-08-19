# ADR-028 — Alpha Response Window & Autonomy Classification

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 3)

## Context

Before a real Alpha run, two truth problems must be solved deterministically: (a) how a loop
completes when no buyer responds, and (b) how a run is classified when unplanned human
intervention occurred. Each was resolved by first asking whether existing canonical state
suffices, and adding only the smallest missing durable fact.

## Decisions

### Response horizon is derived, not duplicated

A market action's response window is NOT a new field. `window_start` is the exact VERIFIED
`MARKET_ACTION` `proof_receipt.created_at`; the duration is the precommitted Gate-3
`validation_test.max_duration_days`; `window_end = window_start + max_duration_days`. No
deadline column is added to `market_action_spec`. `market_window_status(spec, as_of)` derives
`PENDING` / `RESPONDED` / `NO_RESPONSE` / `UNAVAILABLE` from immutable state at an explicit
timezone-aware `as_of` (equality with the deadline counts as elapsed). If there is no verified
action proof or no precommitted duration, the status is `UNAVAILABLE` — never fabricated.

### `NO_RESPONSE` is a deterministic derived fact

`NO_RESPONSE` is established only when the deadline elapsed and no qualifying `REPLIED` was
recorded by it. It is NOT added to the `market_observation` vocabulary and is never ingested as
a provider event. Only `REPLIED` is a qualifying buyer response; `DELIVERED`/`OPENED`/`CLICKED`
are not replies, and `BOUNCED`/`UNSUBSCRIBE` remain independent negative evidence that window
derivation never rewrites. Absence is established by querying canonical state — no caller
`no_response=true`.

### Durable completion only where reconstruction requires it

A `NO_RESPONSE`-driven recommendation cites ZERO observations, so it could not otherwise prove
which completion fact it consumed. `market_window_completion` persists that one immutable
derived fact (kernel-derived over exact proof/duration/window provenance; one per action;
refuses creation before the deadline or when a qualifying response exists; converges on replay).
`recommendation_market_window_completion` binds a recommendation to it, and the completion id
enters the recommendation `input_hash` so new evidence changes recommendation identity. A late
`REPLIED` after the deadline remains real evidence and may drive a NEW later recommendation; the
prior completion and recommendation are retained, never rewritten.

### `NO_RESPONSE` does not imply KILL

The allocator's market-aware branch treats a `NO_RESPONSE` completion exactly like other
inconclusive market evidence toward a still-unresolved precommitted test: the next bounded action
is `MARKET` (another test), never an automatic `KILL`. A precommitted FAIL still yields `KILL`
via the classic path. No market score, no fabricated certainty.

### Autonomy: two distinct dimensions, kernel-derived

`assistance_class` is `CLEAN_AUTONOMOUS_ALPHA` unless the venture has ≥1 `alpha_intervention`.
`alpha_intervention` records ONLY unplanned human correction (reasoning / code / deployment /
provider / outcome-transcription); `PREDEFINED_APPROVAL` is intentionally absent — it is proven
by the immutable `approval` record and never duplicated, so a predefined governance approval
does not invalidate `CLEAN`. The classifier reads durable state; a caller cannot self-certify,
and one venture's interventions never affect another's.

`evidence_class` (`REAL` vs `SIMULATED`) is a SEPARATE dimension. Whether a live provider
transport executed is not yet recorded in canonical state (Slice 2/3 use a fake transport), so
`evidence_class` is conservatively `SIMULATED` and is never faked. `is_clean_autonomous_alpha`
requires BOTH `CLEAN` and `REAL`, so no synthetic fixture can be reported as a real Alpha
success. Recording live-vs-simulated provenance is the smallest future Slice-5 correction.

### No new authority

Window derivation, completion recording, intervention recording, and classification are truth
projections. They create no investment decision, ActionRequest, policy decision, lifecycle
transition, or capital entry, and mutate no observation/proof/spec/validation_test. The allocator
remains the sole author of subsequent decisions.

## Consequences

- Migration `0023` adds `market_window_completion`, `recommendation_market_window_completion`,
  and `alpha_intervention` (all append-only); migrations `0001–0022` unchanged. No
  `closed_loop_run`, no workflow/run engine, no generic audit platform.
- New modules `aidan_core/market/window.py` and `aidan_core/alpha/autonomy.py`; `nextaction.py`
  consumes a persisted `NO_RESPONSE` completion with provenance. No new capability, no provider/
  network, no dependency. The Postmark Slice-2 authenticity boundary is unchanged. Real Alpha
  execution remains a later owner-approved Slice 5.
