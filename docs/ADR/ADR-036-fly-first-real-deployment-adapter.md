# ADR-036 — Fly Machines as the Replaceable First Real Deployment Adapter

**Status:** Accepted
**Gate:** 6/8 — real external deployment boundary

## Context

ADR-023 (Provider-Neutral REAL_EXTERNAL Deployment Contract) froze the minimum contract for a genuine
consequential deployment **without** designating any provider. To prove the first real external
deployment boundary, a concrete provider had to be chosen and implemented as an adapter beneath the
provider-neutral kernel. Candidate providers were researched (Railway, Fly, Render, Cloudflare
Workers); **Fly.io Machines** was selected on the strength of a direct low-level Machines API
(create/get/list/wait/destroy), a free-tier path suitable for a bounded smoke, and clear
image-digest/instance/state semantics.

## Decision

Fly Machines is adopted as the **first** real deployment adapter — a replaceable edge integration,
never architecture. It is implemented as an ordinary deployment worker plus a provider-neutral
observer/verifier seam. The real deployment boundary was proven once, with a bounded live smoke:
one Machine created, health `200` with the expected marker observed, a `DEPLOYMENT_RELEASE` Proof
Receipt VERIFIED, canonical `SUCCEEDED`, correctly bounded lifecycle, confirmed cleanup, governance
delta 0, conservative committed ceiling USD 0.05, secret-leak PASS.

## Consequences

- The kernel's deployment authority (immutable release authority, venture target isolation,
  independent verification, Proof Receipt authority, capital governance, fail-closed retry/recovery,
  secret isolation) is **unchanged**; Fly-specific logic lives only in the adapter.
- Fly may be swapped for another provider by implementing the same provider-neutral seam; no kernel
  change is required.
- Provider ambiguity (e.g. Fly returning HTTP 200 `state=destroyed` for a deleted machine) is handled
  as a state-convergence concern inside the adapter/observer, not by weakening the contract.
- The proven boundary is an **isolated live smoke** (see ADR-039); it does not by itself constitute a
  composed real venture loop.
