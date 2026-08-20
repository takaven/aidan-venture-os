# ADR-035 — Postmark Operational Boundary & Webhook Ingress

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 5, operational boundary — no customer send)

## Context

Slice 4 froze the real-provider execution/provenance machinery (ADR-032/033/034) but left the
*operational* boundary unproven: the frozen module docstring flagged HTTPS + Basic-Auth + IP allowlist
+ POST-only + payload validation as a Slice-5 operational-evidence requirement, and there was no
application-layer request guard in front of the provenance ingestion — `ingest_postmark_event` /
`ingest_postmark_reply` authenticate and reconcile but assume an already-parsed provider event. Slice 5
proves the code-enforceable half of that boundary and a non-consequential provider identity check,
without any customer-facing send.

## Decisions

### A non-consequential provider identity check (`operations.check_provider_identity`)

Reads `GET /server` via the transport and proves the runtime credential belongs to the expected actual
Postmark Server ID AND that `DeliveryType == Live`; the configured MessageStream is checked against the
expectation. It **never sends an email** and never reads the raw token into its result (only the opaque
`credential_ref` is reported). A Sandbox server, a wrong Server ID, or a wrong stream yields
`ready = False`. This is the safe pre-flight before a real market action is ever authorized.

### A minimal application-layer webhook ingress guard (`operations.handle_webhook`)

The smallest guard in front of the frozen ingestion, stdlib-only (no web framework, no new runtime).
In order it enforces: **POST-only** (else 405); an **optional deployment-supplied source-IP allowlist**
(else 403 — off by default, see below); **JSON content-type** (else 415); a **bounded body**
(`MAX_WEBHOOK_BODY_BYTES = 256 KiB`, else 413); **Basic-Auth before parsing the untrusted body**
(else 401); **strict JSON object** (else 400); a **`RecordType` allowlist** `{Delivery, Bounce,
Inbound}` (else 422). Only a request surviving every control is routed — Delivery/Bounce →
`ingest_postmark_event`, Inbound → `ingest_postmark_reply` — each of which independently reconciles the
event to a VERIFIED outbound proof. The guard **writes no canonical lifecycle itself**; a rejection
raises `WebhookRejected(http_status, reason)` and touches nothing. The raw body is never logged.

### Constant-time webhook authentication

`_authenticate` now compares the Basic-Auth header with `hmac.compare_digest` (constant-time) and
raises a distinct `WebhookAuthError` (subclass of `MarketAuthorityError`) which the ingress maps to
HTTP 401, separate from a provenance rejection (422). Behaviour is otherwise identical — exact match
accepted, any mismatch rejected — so no Slice-4 invariant changes.

### Source-IP allow-listing is NOT relied upon (source-confirmed)

Postmark publishes four webhook source IPs but **explicitly does not guarantee they are stable and is
deprecating IP allow-listing** (`static-ip-deprecation`). Therefore no IP range is hard-coded; the
guard accepts an `allowed_ips` list only as optional defense-in-depth for a deployment that can
enforce an authoritative list at the edge. The **primary controls** are HTTPS (network edge) +
Basic-Auth + strict method/content/size/JSON validation + exact provider reconciliation (Slice 4).
Postmark does not sign webhooks (no HMAC), so reconciliation to the hardened outbound proof — not any
transport-level signature — remains the real authority.

### Boundary of this slice

HTTPS termination, POST-only enforcement at the network edge, TLS-reachable deployment, and the real
Postmark account/server/sender-domain verification are **operational** facts that require a real
provider account and a deployed ingress. They are proven with **real operational evidence**, not in
CI, and are explicitly out of scope for the frozen code. Operational configuration/connectivity is
never treated as REAL MARKET evidence.

## Consequences

- No migration (0001–0025 unchanged), no dependency/capability change, no new service/platform.
  Production change is confined to a new `market/operations.py` plus a constant-time/`WebhookAuthError`
  hardening of `market/postmark.py::_authenticate`. Adversarial tests cover identity (Live/Sandbox/
  wrong-server/wrong-stream, zero send), ingress rejections (method/content-type/size/auth/JSON/
  RecordType/IP), accepted routing to REAL observation, provenance rejections (wrong MessageID, no
  hardened proof, foreign/forged reply), and secret non-leakage. Real customer send remains a later
  owner-authorized Slice-6 action, gated on the real operational evidence above.
