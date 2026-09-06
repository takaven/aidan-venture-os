# Frozen Architecture

AI-DAN Venture OS is a governed venture allocator/operator. The fundamental unit is the highest-value next action, not BUILD.

```text
Board / Venture Mandate
        ↓
AIDAN
        ↓
Policy / Capital / Evidence
        ↓
Factory Execution Runtime
        ↓
Replaceable Specialist Workers
        ↓
Verification / Proof Receipts
        ↓
Market + Operate Runtime
        ↓
Outcomes / Portfolio Learning
```

## Four planes

- **Governance:** Venture Mandate, policy, approvals, autonomy, capital controls and permitted state transitions.
- **Intelligence:** AIDAN opportunity discovery, research synthesis, Kill Case, assumptions, experiments, investment decisions and next-best-action allocation. AIDAN does not write application code.
- **Execution:** durable Factory runtime, replaceable specialist workers, product/market work, QA, deployment, rollback and operations.
- **Truth:** Evidence Ledger, Capital Ledger, Audit Ledger, canonical venture state, experiments, outcomes, costs and machine-verifiable Proof Receipts.

## Locked execution doctrine

Consequential actions follow:

`ActionRequest → Policy → Approval if required → Execution → Proof Receipt → permitted canonical transition`.

Generated prose and worker self-report do not establish consequential success. Deterministic verification outranks agent self-assessment. PostgreSQL is the intended canonical durable state from Gate 1 onward; no critical state may exist only in memory.

Each venture is an isolated repository, credential, data, deployment, permissions and budget boundary. Historical repositories remain donors and cannot become canonical by bulk merge.

> This document describes the **timeless, frozen** architecture. Fast-changing programme state (which
> gate is active, what has been proven, the current blocker) lives in `docs/PROGRAMME_STATUS.md`, not
> here.

## Highest-value-next-action doctrine

The fundamental unit of work is the **highest-value next action**, not BUILD. AIDAN is the persistent
executive/capital allocator; it may discover, evaluate, fund, build, validate, operate, continue,
pivot, or kill ventures. Building an application is only ever *one* possible next action, chosen when
the evidence justifies it — never the default. AIDAN itself does not author application code; code
authoring is a replaceable specialist worker beneath its authority.

## Capital-scarcity doctrine

AIDAN must be useful under **severe capital scarcity**. It preferentially discovers the *cheapest*
action capable of materially changing a capital decision, optimizing **decision value / uncertainty
reduction per dollar**. Realistic capital frames are on the order of $20–$2,000, not tens of
thousands. Weak ventures should die **cheaply**. (See ADR-037.)

## Replaceable worker / provider adapters

Every external system — research provider, coding worker, deployment provider, market channel — is a
**replaceable adapter at the edge**, never architecture. The kernel (governance, capital, evidence,
proof, lifecycle) is provider-neutral. Concrete adapters proven to date include Tavily/Anthropic
(research), OpenAI Codex (coding worker), Fly Machines (deployment — ADR-036), and Postmark (market
channel — ADR-027/032/033/034/035). Any of these may be swapped without touching kernel authority.

## Reality / proof maturity model

Consequential capability matures along a ladder:

```
DETERMINISTIC → CONTRACT-PROVEN → LIVE-SMOKED → COMPOSED-REAL → QUALIFYING-REAL
```

An **isolated live boundary smoke** (LIVE-SMOKED) proves one real edge works once; it is *not* the
product. The product requires **COMPOSED-REAL** — multiple real boundaries composed into one governed
end-to-end venture loop — and ultimately **QUALIFYING-REAL** outcomes that move portfolio value.
Deterministic verification outranks agent self-assessment at every rung. (See ADR-039.)

## Venture Substrate vs Market Runtime vs Operate Runtime

- **Venture Substrate** — the build-side substrate: venture repository, release candidates, build
  quality/anti-generic gate, deployment targets, and the proof-gated `BUILDING → OPERATING`
  transition.
- **Market Runtime** — governed market *actions* (e.g. a Postmark send) with a deterministic
  MARKET_ACTION verifier and Proof Receipt: the action proves *the exact authorized message was
  accepted by the provider*, which is distinct from any commercial outcome.
- **Operate Runtime** — ongoing operation, market *observations* (delivery/bounce/reply as separate
  evidence), and the continue/pivot/kill decision surface.

Action proof (the exact email was accepted) is **not** a market outcome. Delivery, reply, demand and
revenue are later, separate evidence in the Operate Runtime — never inferred from a successful send.

## Operate substrate: Grok-substitution rule

Do **not** build a large generic browser/computer-use/SaaS integration substrate by default. When
persistent browser/computer/SaaS operation first becomes concretely necessary, run a bounded **Grok
Bot substitution spike** first; Grok is a candidate *replaceable execution substrate inside* AIDAN
governance and never replaces mandate, evidence, Kill Cases, capital allocation, policy, approvals,
Proof Receipts, or lifecycle authority. (See ADR-038.)

## Infrastructure proof is not venture judgment

A crucial distinction the programme must hold: proving that an external **boundary** works
(research/coding/deploy/market-send) is *execution/governance plumbing*. It is necessary but **not
sufficient**. The north-star thesis — that AIDAN can select attractive opportunities, kill weak ones,
and allocate scarce capital *well* — is a **judgment** claim that no boundary smoke can establish. The
architecture is designed so that judgment quality can eventually be evaluated against evidence and
outcomes, not against document or infrastructure completeness.
