# ADR-007 — Source Receipts & Provenance

**Status:** Accepted
**Gate:** 2 — Autonomous Research (Slice 1)

## Context

Gate 2 builds an evidence-backed research layer. Slice 1 establishes immutable,
provider-neutral Source Receipt provenance *before* any interpretation, claim or
opportunity generation. This ADR records the durable decisions; it does not
reopen the frozen architecture.

## Decisions

### One canonical evidence envelope

Gate 1's `evidence_record` remains the canonical evidence identity and the
**authoritative holder of `content_hash`**. Gate 2's `source_receipt` is a
strict 1:1 SOURCE **subtype**, not a competing evidence store. A composite
foreign key `(evidence_record_id, venture_id, source_kind) → evidence_record
(id, venture_id, kind)` enforces, in one declaration, that every Source Receipt
has exactly one SOURCE `evidence_record` parent, with an agreeing venture. The
same envelope pattern is intended for Observation and Claim in later slices.

Authoritative locations (no ambiguous duplication):
- content hash → `evidence_record.content_hash`;
- locator → `source_receipt.locator` (`evidence_record.source_ref` is unused for
  SOURCE envelopes);
- SOURCE-specific metadata → `source_receipt` (`evidence_record.payload` is empty
  for SOURCE envelopes).

### Adapter returns untrusted data only

A `ResearchAdapter` acquires source DATA only — it never returns canonical ids,
Observations, Claims, Opportunities, policy decisions or ActionRequests, and
never writes PostgreSQL. Acquisition and canonical ingestion are separate
operations. Provider/vendor names appear only as data (`retrieved_by`), never in
schema or table names.

### Deterministic ingestion is the canonical write boundary

`sources.ingest` is the single path acquired material enters canonical state: it
hashes the content, enforces acquisition idempotency, and atomically creates the
SOURCE `evidence_record` envelope, its `source_receipt`, and an audit event.

### Acquisition idempotency ≠ retrieval history

Idempotency is keyed on `(venture_id, acquisition_key)`, **not**
locator+content. Semantics:
- same acquisition key + identical content → the same receipt (idempotent retry);
- same acquisition key + different content → hard conflict (no replacement);
- **new** acquisition key + same locator + same content (later `retrieved_at`) →
  a **new** receipt — a re-check is itself material provenance, and earlier
  retrievals are never erased;
- new acquisition key + same locator + changed content → a new receipt; the old
  version is preserved (source mutation history).

### Content hash proves identity, not truth

`content_hash` is the SHA-256 of the exact acquired content; changed content
yields a changed hash. There is no content-addressable storage system and no
default full-page archival — only bounded excerpt/snapshot references where
separately permissible.

### Freshness is derived, never frozen

The immutable receipt persists only objective temporal inputs (`retrieved_at`,
`published_at?`, `publication_time_known`). Freshness (`CURRENT / STALE /
UNCERTAIN_FRESHNESS`) is computed by a pure helper given an explicit
`(as_of, max_age)` context. There is **no universal freshness threshold** and no
persisted freshness value.

### Reliability: intrinsic categories only

An optional intrinsic `reliability_code` (e.g. PRIMARY/SECONDARY,
AUTHORITATIVE/ANECDOTAL, DIRECT_MEASUREMENT/COMMENTARY, UNKNOWN) may be recorded
at acquisition time. `CORROBORATED` is deliberately excluded — it depends on
relationships between multiple records and belongs to later Claim/contradiction
logic. There is no decimal trust score.

### Validation dependency

Untrusted acquisition input is validated with the **standard library** (a frozen
dataclass with explicit checks). For Slice 1 this is as small and robust as a
third-party model, so no dependency (e.g. Pydantic) was added; that choice can be
revisited when acquisition envelopes grow.

### Source content has no authority

Acquired content is DATA. Instructions embedded in it cannot alter the Venture
Mandate, policy, kill switch, autonomy, budget, ActionRequest status, tools,
secrets, deployment or repositories — the research path can only append evidence
records through the governed kernel, and consequential actions still flow through
Gate 1's `ActionRequest → Policy`.

## Consequences

- Later slices add Observation/Claim/Interpretation/Assumption/Opportunity/Kill
  Case on this provenance substrate, reusing the envelope pattern.
- Migrations `0001–0004` remain unchanged; `0005` adds only Slice 1 schema.
