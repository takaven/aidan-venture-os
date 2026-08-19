# ADR-024 — Market Action Proof & External Observation

**Status:** Accepted
**Gate:** 7 — Market Runtime (Slice 2)

## Context

Slice 1 froze market action authority. Slice 2 executes an exactly authorized market
action against a deterministic controlled channel, proves the action occurred, and
captures externally-observed outcomes as evidence — without letting worker/model claims
manufacture market truth.

## Decisions

### Three distinct truths

- **Authorized intent** — `market_action_spec` (exact content/audience/offer/spend/channel).
- **Action-occurred proof** — a `MARKET_ACTION` `proof_receipt` means the controlled
  channel accepted/executed that exact authorized action. It does NOT prove any market
  outcome (delivered/read/replied/converted/WTP).
- **Market observation** — a later externally-attributable outcome (`market_observation`).

Action proof and market observation are kept strictly separate; a later observation never
mutates the action proof.

### Deterministic controlled channel; kernel-verified, worker-inert

A channel worker (an ordinary Gate-4 `WorkerAdapter`) materializes the exact authorized
outbound envelope + a deterministic acceptance id into a venture/source-scoped LOCAL
outbox. The forced `market-action` verifier independently reads the outbox and requires
ALL of `ACTION_EXISTS`, `ACTION_IDENTITY` (envelope content re-hashed vs the frozen
`content_hash` + `action_spec_hash`), `AUDIENCE_IDENTITY`, `CHANNEL_IDENTITY`
(channel + source instance), and `ACCEPTANCE_IDENTITY` (acceptance attributable to the
exact attempt) — no score. A worker's `sent`/`delivered`/`replied`/`payment_succeeded`
claim is inert, and execution SUCCESS is not action proof. The controlled channel proves
architecture only — NOT real external delivery (no network, no credentials, no provider).

### One proof system; existing capital flow

A VERIFIED market action produces the ONE canonical `proof_receipt`
(`verification_type=MARKET_ACTION`) bound to the exact action + attempt + spec + acceptance
— no second proof/retry system. Retries reuse Gate-4 (same immutable spec, new attempt).
Spend uses the existing capital ledger: the zero-cost local channel commits nothing; a
worker cannot fabricate committed spend (`actual_cost` is kernel-supplied, and committed
spend cannot exceed the authorized reservation).

### External observations are append-only, source-scoped evidence

`market_observation` records an external outcome from a finite vocabulary
(`DELIVERED/BOUNCED/OPENED/CLICKED/REPLIED/UNSUBSCRIBE`). Dedupe is SOURCE-SCOPED —
`UNIQUE(venture_id, channel_kind, source_instance_ref, external_event_id)` — because
provider event ids are unique only within an account/channel instance; the same textual id
under a different source instance may coexist, an identical duplicate converges, and a
materially different payload for the same scoped id conflicts. Evidence hashes are
kernel-derived; observations bind to the exact venture/action and reject wrong
channel/source/cross-action. Negative evidence (BOUNCED/UNSUBSCRIBE) is first-class and
never overwritten.

### `NO_RESPONSE` is derived, not ingested

Absence is not an external event: `NO_RESPONSE` is not in the vocabulary and is rejected at
ingestion. A Slice-3 deterministic derivation may conclude it after a frozen observation
window.

### Untrusted payload; no market decision authority

An observation payload is data, not authority: it cannot execute commands, change
Policy/lifecycle, write an investment decision, or trigger a new action. Observations may
arrive asynchronously (after the action completed) and remain recordable after a kill
switch is engaged — a kill blocks NEW consequential actions but never erases historical
external evidence. No interpretation/classification occurs in Slice 2.

## Consequences

- Migration `0020` adds `market_observation` (source-scoped dedupe, finite vocabulary,
  append-only); migrations `0001–0019` unchanged.
- New production modules `aidan_core/market/{channels,verifiers,observation}.py` and a
  forced market verifier + `verify_market_action`. `prepare_market_execution`/
  `execute_market_action` no longer take a `verifier_kind`.
- No interpretation/metrics/decision/CRM/payment, no real send, no network, no dependencies.
