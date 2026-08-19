# ADR-023 — Governed Market Action Authority

**Status:** Accepted
**Gate:** 7 — Market Runtime (Slice 1)

## Context

Gate 6 closed with OPERATING ventures (verified deployed runtime). Gate 7 begins Market
Runtime. Slice 1 establishes the authority boundary between an OPERATING venture and any
external market action, without sending anything.

## Decisions

### validation_test remains the commercial source; market_action_spec freezes execution intent

Gate 3's `validation_test` already owns the commercial hypothesis, precommitted
success/kill criteria, WTP modality, and the experiment-level `max_spend`. Gate 7 does
NOT duplicate that. A new immutable `market_action_spec` freezes only the exact
*executable* intent of one market action — channel, audience, exact content, offer/price,
and this action's `authorized_spend_amount` — and references the `validation_test` and
`opportunity` by FK for provenance. `market_action_spec` carries no success/kill criteria
and no `max_spend`.

### An OPERATING venture with genuine commercial provenance is required

`create_market_action_spec` requires the venture to be `OPERATING`, the action to be a
canonical market action (`action_type='send_outreach'`), and the referenced
`validation_test` to belong to the same venture and to the exact opportunity (via its
hypothesis). No new buyer/problem/commercial thesis is invented at execution time. A
BUILDING venture, a non-market action, or cross-venture provenance is rejected. Multiple
market actions may legitimately reference the same `validation_test`; each still gets its
own immutable 1:1 spec.

### Exact content/offer/spend are frozen; the worker cannot substitute them

The exact message `content` is frozen with a kernel-derived `content_hash`; an offer/price
is frozen where present. A one-byte content change, or any changed audience/channel/offer/
price/spend/provenance, is a hard `IdempotencyConflictError` — never a silent mutation
(the row is immutable). Generated prose is a proposal until frozen here.

### Spend is bounded, not duplicated

`authorized_spend_amount` is proven ≤ the referenced `validation_test.max_spend` (or must
be zero if none is precommitted) AND ≤ canonical available budget (granted − reserved −
committed), and must equal the governed ActionRequest's `requested_amount`. The canonical
capital ledger is reused; no second budget system is added, and no spend is reserved or
committed in Slice 1 (external action does not occur).

### Authority is enforced at the canonical execution-spec boundary

As with the Gate-5 BUILD and Gate-6 deploy guards, the market authority lives at
`factory.spec.create_execution_spec`, not only in a helper: a canonical market action's
execution spec may only be created when the task payload binds the exact
`market_action_spec` id + `action_spec_hash` + frozen channel + audience — for any caller.
So a market action can never be executed from free-form intent, and the channel worker
cannot substitute channel/audience/content. Non-market actions are unaffected. Dispatch
authorization is fresh and post-spec (Gate-4 chronology); the kill switch blocks dispatch.

### The channel worker is a Gate-4 WorkerAdapter with no market authority

There is no `MarketAdapter`/second runtime. A channel worker is an ordinary WorkerAdapter
(capability `SEND_OUTREACH` only); its result — including `sent`/`replied`/
`payment_succeeded`/`lifecycle`/`investment_decision` claims — is inert. Slice 1 creates
no market observation, no interpretation, no market proof, and no investment/lifecycle
mutation, and performs NO external send (a deterministic fake worker proves the boundary).

## Consequences

- Migration `0019` adds `market_action_spec` and extends the `execution_spec` capability
  CHECK with `SEND_OUTREACH`; migrations `0001–0018` unchanged.
- New production package `aidan_core/market/` (`action`, `runtime`); one new error
  `MarketAuthorityError`; `factory/spec.py` gains the market guard + `SEND_OUTREACH` in its
  capability vocabulary.
- No market observation/interpretation/metrics/proof/CRM/payment/provider, no external
  send, no network, no dependencies. Those are Slice 2+/later concerns.
