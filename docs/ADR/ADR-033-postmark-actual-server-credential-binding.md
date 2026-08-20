# ADR-033 — Postmark Actual-Server Credential Binding

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 4, ZIP-audit correction)

## Context

ADR-032 froze the non-secret provider identity, but the audit found the frozen opaque
`credential_ref` (and a field named `server_id` that was actually sent as Postmark
`MessageStream`) was never bound to the raw token injected into `PostmarkHttpTransport`. So a
coordinated substitution could still execute and verify against a *different* Postmark server
while the frozen `PostmarkSource` metadata matched — the same class of defect one level deeper.
Postmark distinguishes the server token, the Postmark **Server ID** (`GET /server` → `"ID"`), and
the **MessageStream** id; the old model conflated stream and server.

## Decisions

### Distinguish the actual server from the message stream

`PostmarkSource` now carries `postmark_server_id` (the actual Postmark Server ID from
`GET /server`, non-secret) and `message_stream` (e.g. `outbound`) as separate fields. The frozen
contract and `source_identity = postmark:<credential_ref>:<postmark_server_id>` use the actual
server id, never the stream. `send_email` sends `MessageStream = message_stream`; provider
reconciliation compares the provider's `MessageStream` to the frozen `message_stream` and never
mislabels it a Server ID.

### The runtime token must prove it belongs to the frozen server

`PostmarkHttpTransport.get_server_identity()` calls the server-scoped `GET /server`
(`X-Postmark-Server-Token`) and returns the actual Server `ID`. Before any `POST /email`, the
worker requires `transport.get_server_identity() == frozen postmark_server_id` (in addition to
the frozen config check) — a matching `credential_ref` string is **not** sufficient. A wrong
runtime token (belonging to another server) is rejected before send, even if `credential_ref`,
sender, stream, and recipient all match.

### The verifier independently checks the actual server

`PostmarkActionVerifier` adds `SERVER_IDENTITY` = `transport.get_server_identity() ==
frozen postmark_server_id`, and `CHANNEL_IDENTITY` now checks the provider `MessageStream`. A
verifier handed a transport whose token belongs to another server rejects the action even when the
exact message is present.

### The REAL attestation carries the actual server + stream

`_PostmarkVerifiedProviderState` now carries `server_id` (from `GET /server`) and
`message_stream`. `verify_postmark_action` and observation ingestion bind `REAL_PROVIDER` only
when the attested server AND stream match the frozen contract; otherwise SIMULATED. Server
identity is never derived from the message stream.

### `credential_ref` semantics

`credential_ref` remains canonical only as the opaque OS secret **handle** approved for the
action; it is explicitly not treated as proof of which server the runtime token belongs to. No
secret manager, no account-level token, no provider-account/credential table.

### Webhook IP allowlist is operational, not code

The module docstring is corrected: this code enforces Basic-Auth + independent reconciliation;
Postmark's recommended IP allowlist is an ingress/firewall boundary that is NOT enforced here and
is a Slice-5 operational-evidence requirement (HTTPS + Basic-Auth + Postmark IP allowlist +
POST-only + payload validation) — the comments do not prove it exists.

## Consequences

- No migration (0001–0025 unchanged), no dependency/capability change, no provider-account/secret
  subsystem. Production change confined to `market/postmark.py`. Test fakes model `GET /server`
  with distinct synthetic servers; adversarial tests prove a wrong-server token is rejected
  pre-send and at verification, and that the attestation carries the actual server/stream. Real
  Alpha execution remains a later owner-approved Slice 5, gated on a fresh pre-freeze ZIP audit.
