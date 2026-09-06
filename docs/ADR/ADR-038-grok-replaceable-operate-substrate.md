# ADR-038 — Grok as a Replaceable Operate/Execution Substrate (Spike-First)

**Status:** Accepted
**Gate:** Operate Runtime (deferred until concretely needed)

## Context

Operating real ventures will eventually require persistent browser / computer-use / SaaS operation
(navigating web apps, driving third-party tools, performing repetitive operational tasks). The naive
response is to build a large generic browser/computer-use/SaaS integration substrate up front. That
is expensive, speculative, and risks becoming architecture in its own right before it is justified by
a real venture need.

## Decision

- **Do not build a large generic browser/computer-use/SaaS integration substrate by default.**
- When persistent computer/SaaS operation first becomes **concretely** necessary, run a **bounded Grok
  Bot substitution spike** first.
- Grok is a candidate **replaceable execution substrate** that operates *inside* AIDAN governance.
- Grok does **not** replace, and is never allowed to replace: the Venture Mandate, the evidence model,
  Kill Cases, capital allocation, policy, approvals, Proof Receipts, lifecycle authority, or
  continue/pivot/kill decisions.

Preferred sequence: market ingress → thin real venture loop → the need for persistent computer/SaaS
operation becomes concrete → bounded Grok substitution spike → adopt Grok if adequate → otherwise
build **only** the missing capability.

## Consequences

- Operate-runtime capability is acquired **lazily and cheaply**, consistent with the capital-scarcity
  doctrine (ADR-037).
- If Grok is adopted, it is an edge adapter beneath the kernel's governance/evidence/proof authority,
  swappable like any other provider.
- No generic operate substrate should be merged as infrastructure ahead of a real venture need.
