# ADR-032 — Frozen Postmark Source & Recipient Authority

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 4, ZIP-audit correction)

## Context

A full-source audit found that the exact Postmark provider source (server/stream, sender,
credential identity) and the resolved recipient were runtime configuration shared by the worker
and the verifier, not immutable canonical execution authority. Because both sides consumed the
same runtime `PostmarkSource`/`RecipientResolver`, a coordinated substitution stayed internally
self-consistent and VERIFIED — the verifier proved worker/verifier agreement, not that the sent
message matched the identity approved when the action was frozen. That breaks the Slice-5
requirement that the consequential action be frozen before owner approval and executed unchanged.

## Decisions

### Freeze the exact non-secret provider identities at preparation

`prepare_postmark_execution` now resolves and freezes, into the immutable `execution_spec`
(`task_payload` + `expected_output_contract`), a `postmark.source` block —
`{provider_kind, server_id, sender, subject, credential_ref, inbound_domain, source_identity}`
(a concrete `source_identity = postmark:<credential_ref>:<server_id>`) — and a `recipient_hash`
= SHA-256 of the normalized resolved recipient. No raw server token or webhook secret ever enters
canonical state; only the opaque `credential_ref` is frozen. The recipient is stored as a
deterministic hash, so there is no plaintext PII in the execution spec.

### The worker obeys the frozen contract, not its runtime config

The worker's `PostmarkSource`/`RecipientResolver` are I/O dependencies, not authority.
`PostmarkEmailWorker.execute` refuses to send — before any provider call — unless its runtime
`server_id`, `sender`, `subject`, and `credential_ref` equal the frozen source AND the resolved
recipient's hash equals the frozen `recipient_hash`. It sends using the frozen sender/server/
subject. A source/sender/credential/recipient substitution is blocked pre-send.

### The verifier's expected values come from the immutable contract

`PostmarkActionVerifier(transport)` no longer takes a runtime source/resolver. Every expected
value — server (`CHANNEL_IDENTITY`), sender (`SENDER_IDENTITY`), recipient
(`AUDIENCE_IDENTITY` via `recipient_hash`), content, correlation, acceptance — is read from
`expected_output_contract`. A runtime `PostmarkSource`/`RecipientResolver` cannot redefine the
expected authority.

### Reply attribution uses the frozen recipient

`ingest_postmark_reply` no longer takes a resolver: after MailboxHash correlation, the inbound
`From` is checked against the executed action's frozen `recipient_hash`. A later runtime resolver
cannot redefine who the authorized buyer was; a reply from any other sender is rejected.

### Evidence origin binds the concrete frozen source

Both the outbound `external_evidence_origin` and the observation `market_observation_origin` bind
the concrete frozen `source_identity`, and a REAL attestation is accepted only when the attested
provider server matches the frozen approved server. The Gate-7 `market_observation` dedupe key
(`source_instance_ref = postmark-email:<venture>`) is unchanged, preserving Gate-7 semantics; the
concrete provider identity is carried in the frozen contract and the origin rows.

### Owner-approval seam

The immutable action/execution state after this correction is sufficient to reconstruct the
approved non-secret identities the Slice-5 owner checkpoint must review — recipient (hash),
sender, provider/source identity, content/offer — without a provider-management subsystem.

## Consequences

- No migration (0001–0025 unchanged), no dependency/capability change, no provider-configuration
  architecture. Production change confined to `market/postmark.py`. New adversarial tests prove
  source/sender/credential/recipient substitution is blocked before send, the verifier authority
  is the frozen contract, and no raw token appears in canonical state. Real Alpha execution
  remains a later owner-approved Slice 5; this candidate must pass a fresh pre-freeze ZIP audit.
