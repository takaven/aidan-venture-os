# ADR-030 — Real Market-Observation Provenance

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 4)

## Context

Slice 4 made the outbound action's evidence origin durable (`external_evidence_origin`, ADR-029)
and, as an interim measure, restricted a REAL loop outcome to a `NO_RESPONSE` completion anchored
to a real action proof. That is too narrow: a genuine provider event (reply, bounce, unsubscribe)
before the deadline is real market evidence, and it must be classifiable as REAL — otherwise only
silence could produce REAL while an observed response would paradoxically read SIMULATED. This ADR
adds the smallest durable observation-origin binding so provider-backed outcomes are provable.

## Decisions

### A real action proof does not make an outcome real

Reality is a property of the OUTCOME evidence, not merely the outbound action. Canonical state now
distinguishes a trusted, authenticated/reconciled provider observation from a generic one via
`market_observation_origin` (migration 0025): one append-only binding per observation, tying it to
its venture, market action spec, and the action's `MARKET_ACTION` proof.

### Provider-backed observations require durable trusted origin; generic fails closed

`market_observation_origin.origin_kind` is `REAL_PROVIDER` only when written by the trusted Postmark
ingestion path AND the observation's action already carries a `REAL_PROVIDER` action proof. A
generic `record_market_observation` call writes NO origin row; absence means SIMULATED (fail-closed).
So a generic REPLIED on a REAL-provider action stays SIMULATED, and no metadata/event field can
change that.

### `REAL_PROVIDER` cannot be caller-selected

`record_observation_origin` has no `origin_kind` argument. The value is derived INSIDE the writer
from the actual transport type (`REAL_PROVIDER` only for a genuine `PostmarkHttpTransport`, whose
verification path makes real provider calls) and is downgraded to SIMULATED unless the action's
`external_evidence_origin` is `REAL_PROVIDER`. No `is_real=True`, webhook-JSON field, WorkerResult
flag, raw-evidence field, or source-ref naming can set it.

### Postmark positive and negative events can be REAL; NO_RESPONSE stays a separate real path

`Delivery → DELIVERED`, `Bounce → BOUNCED`, and a correlated `Inbound → REPLIED` bind a
`REAL_PROVIDER` observation origin when ingested through the trusted path. Reply text remains
untrusted DATA. A `NO_RESPONSE` completion anchored to a `REAL_PROVIDER` action proof remains a
distinct real path (no observation-origin row needed — it derives from the real action proof plus
canonical window absence). Commercial positivity is unrelated to reality class: a real bounce is
still REAL.

### Loop reality derives over the exact cited outcome

`classify_loop` derives `reality_class = REAL` only when the (validated) next recommendation cites,
for THIS market action, either (A) an observation with a `REAL_PROVIDER` `market_observation_origin`,
or (B) a `NO_RESPONSE` completion anchored to a `REAL_PROVIDER` action proof. An unrelated REAL
observation elsewhere in the venture cannot upgrade the loop. `eligible_clean_real_alpha` still
requires COMPLETE + CLEAN + REAL.

### No new authority; no second event system

Observation-origin recording is provenance about evidence: it creates no ActionRequest, policy
decision, investment decision, lifecycle transition, capital entry, validation_result, proof, or
market_action_spec, and mutates no observation. Existing source-scoped `market_observation` dedupe
remains the sole event-identity mechanism; a webhook retry converges to one observation and one
origin row. No manual outcome-import path is added; a human-transcribed outcome would be recorded
as an `alpha_intervention` (HUMAN_ASSISTED) and cannot receive `REAL_PROVIDER` provenance.

## Consequences

- Migration `0025` adds `market_observation_origin` (append-only); migrations `0001–0024` unchanged.
- `market/origin.py` gains `record_observation_origin` + `observation_is_real`; the Postmark
  ingestion path binds the origin; `alpha/loop.py` derives reality over cited REAL provenance
  (path A) or a REAL-anchored NO_RESPONSE (path B). No capability, provider account, credential,
  network, or dependency change; no market/autonomy score. Real Alpha execution remains a later
  owner-approved Slice 5.
