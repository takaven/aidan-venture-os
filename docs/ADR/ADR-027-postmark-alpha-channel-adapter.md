# ADR-027 — Postmark Alpha Channel Adapter

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 2)

## Context

Gate 8 Slice 1 connected market evidence to the allocator. Slice 2 adds the first concrete
real-channel adapter so a `send_outreach` market action can eventually reach a real recipient.
Postmark email is chosen as the single Alpha channel; it remains an adapter at the edge, never
architecture. Slice 2 implements and deterministically verifies the adapter contract with
synthetic transports/credentials/events and performs NO real send.

## Decisions

### Postmark is one replaceable WorkerAdapter

`PostmarkEmailWorker` is an ordinary Gate-4 `WorkerAdapter`; `PostmarkActionVerifier` is an
ordinary deterministic `Verifier`. No `ProviderRuntime`/`OutreachAdapter`/`ChannelAdapter`
hierarchy, no second `WorkerRegistry`, and no second retry engine are introduced. The provider
name appears only inside `market/postmark.py` and its configuration.

### Provider MessageID is acceptance identity; the worker's claim is inert

Postmark's send API returns a `MessageID`, recorded as the action's acceptance identity. A
worker returning a MessageID is NOT proof. Canonical `MARKET_ACTION` verification is independent:
the verifier queries provider state **by opaque canonical correlation Metadata**
(`venture` + `market_action_spec` + `action_request` + `action_spec_hash`), retrieved via
Postmark's outbound message-details API (`GET /messages/outbound/{id}/details`, exposing
To/From/Subject/TextBody/Metadata). It checks ACTION_EXISTS, ACTION_IDENTITY (plain-text
`TextBody` hash vs the frozen `content_hash` + `action_spec_hash`), AUDIENCE_IDENTITY (provider
recipient vs the resolver's authorized recipient), CHANNEL_IDENTITY (Postmark server/source),
SENDER_IDENTITY (provider `From` vs the source-authorized sender), and ACCEPTANCE_IDENTITY
(observed MessageID == the result's claimed id). All required, no score. A worker that lies about
its MessageID, sends nothing, or tampers body/recipient/sender/source is REJECTED and produces
no proof. The existing single `proof_receipt` (`verification_type=MARKET_ACTION`) is reused.

### Action proof ≠ market outcome; events normalize to existing observations

A VERIFIED action proves only that the exact authorized email was accepted by Postmark. Later
outcomes are separate `market_observation` evidence: `Delivery → DELIVERED`, `Bounce → BOUNCED`,
and an inbound correlated reply `→ REPLIED`. No new event/truth table, no
QUALIFIED_REPLY/MARKET_SUCCESS/PAYMENT/NO_RESPONSE. Reply text is stored raw and remains
untrusted DATA (no positive/qualified/WTP classification).

### Reply correlation is structural (MailboxHash)

Each outbound action sets a deterministic, opaque `Reply-To` MailboxHash derived from
`(venture, market_action_spec)` — no PII, no secret. An inbound reply is correlated to exactly
one market action by that token (never by subject/body similarity); a wrong/foreign/nonexistent
token is rejected, preserving venture isolation.

### Authenticity boundary: Basic-Auth + independent reconciliation (no HMAC)

Postmark explicitly does **not** sign webhooks (no HMAC). Its official boundary is HTTP
Basic-Auth on the webhook URL plus IP allowlisting. Provider events are therefore authenticated
by a shared Basic-Auth secret AND reconciled against provider state / canonical correlation
before becoming evidence; an unauthenticated arbitrary POST never becomes canonical evidence.
This is a real, documented limitation (weaker than a cryptographic signature), mitigated by the
independent API reconciliation.

### Real-send safety = RECONCILABLE

A Postmark submission is not proven exactly-once by the API result alone. The send is classified
`RECONCILABLE`: an ambiguous result is reconciled against provider state via the opaque
correlation Metadata before any retry — never a blind duplicate send. Retries reuse Gate-4
(same immutable `market_action_spec`, new `execution_attempt`).

### No secrets in canonical state; no live operation in Slice 2

No API token or webhook secret appears in the repo, migrations, `market_action_spec`,
`execution_spec` task payload, logs, tests, or this ADR. The production `PostmarkHttpTransport`
receives its server token by trusted runtime injection and is never exercised by the suite.
Live credentials, sender-signature/domain setup, recipient authorization, webhook deployment,
and network execution remain later owner-approved Slice-5 dependencies.

## Consequences

- New production module `aidan_core/market/postmark.py` (worker + verifier + transport
  protocol + event normalization + prepare/execute/verify). No migration (0001–0022 unchanged),
  no new capability (`SEND_OUTREACH` remains sole), no dependency (stdlib `urllib` only, unused
  in tests), no provider architecture inward of the adapter edge.
