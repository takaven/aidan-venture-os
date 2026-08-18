# ADR-008 — Observations, Claims & Contradictions

**Status:** Accepted
**Gate:** 2 — Autonomous Research (Slice 2)

## Context

Slice 2 extends the canonical Gate 2 evidence model from Source Receipts to
provenance-linked Observations, Claims, and explicit SUPPORTS/CONTRADICTS
relations, with deterministic derived Claim state and preserved contradictory
evidence. This ADR records the durable decisions; it does not reopen the frozen
architecture.

## Decisions

### One canonical evidence envelope

SOURCE, OBSERVATION and CLAIM all share the Gate 1 `evidence_record` identity.
`observation` and `claim` are 1:1 append-only subtypes, each bound by a composite
foreign key `(evidence_record_id, venture_id, kind) → evidence_record` that
enforces parent existence, venture agreement, and the correct kind. No competing
evidence store is introduced. Authoritative `content_hash` lives on the envelope.

### Observation requires exact Source Receipt provenance

An `observation` cannot exist without an exact Source Receipt of the same
venture, enforced by a composite FK `(source_evidence_id, venture_id) →
source_receipt`. Observations carry directly-observed statements only — no
interpretation, assumption, recommendation, opportunity or decision, and no
confidence score.

### Provenance verification ≠ semantic truth verification

Slice 1 retains only a bounded excerpt + content hash, not full source text, so
textual occurrence of an observed statement in the source is **not**
deterministically verified. Slice 2 verifies **link integrity** only (Source
Receipt identity, venture consistency, content-hash lineage, typed provenance
relationship). Textual source verification is claimed only where retained
material genuinely permits it — which is not the case here — so it is neither
claimed nor tested.

### Claim is a proposition, not truth; state is derived

A `claim` is a proposition and is never automatically true. There is no API to
set claim truth. Support state is **derived structurally** from append-only
relations, never persisted as caller-controlled truth:

- **UNSUPPORTED** — no SUPPORTS and no CONTRADICTS;
- **SUPPORTED** — ≥1 SUPPORTS and no CONTRADICTS;
- **CONTRADICTED** — ≥1 CONTRADICTS and no SUPPORTS;
- **DISPUTED** — ≥1 SUPPORTS and ≥1 CONTRADICTS.

Later evidence changes the derived state without mutating any prior relation.

### Explicit stances; contradictions preserved

`claim_evidence` records explicit `SUPPORTS`/`CONTRADICTS` relations, append-only.
One stance is permitted per `(claim, observation)` pair: an exact retry is
idempotent; the same pair re-asserted with the **opposite** stance is a
deterministic conflict, never a silent replace (a single observation cannot both
support and contradict the same claim). Contradiction at the *claim* level arises
from *different* observations, and both — with their Source Receipts — are
preserved. New evidence never overwrites, suppresses or deletes prior
contradictory evidence.

### Freshness and reliability qualify, never mutate

Freshness remains derived from immutable temporal metadata plus an explicit
`(as_of, max_age)` context; it is never persisted as universal truth, never used
to delete or hide stale evidence, and never rewrites a stance. A stale SUPPORTS
plus a current CONTRADICTS still derives DISPUTED. Intrinsic reliability codes
qualify evidence; there is no decimal trust score and no assumption that
AUTHORITATIVE = true.

### No new authority; no new dependency

Observation/Claim operations persist research-truth primitives only; they have no
authority over Mandate, Policy, kill switch, autonomy, capital, ActionRequest
success, Proof Receipts, tools, deployment, repositories or secrets. Slice 2
added no dependency (stdlib-only, consistent with Slice 1). No Interpretation,
Assumption, Opportunity or Kill Case is implemented in this slice.

## Consequences

- Slice 3 Interpretation may reason over contradictions but may not mutate
  evidence.
- Migrations `0001–0005` remain unchanged; `0006` adds only Slice 2 schema.
