# ADR-004 — Canonical State Separation & Idempotent Action Intake

**Status:** Accepted
**Gate:** 1 — Truth & Governance (Slice 2)

## Context

Slice 2 introduces the first venture/action domain primitives on the Slice 1
PostgreSQL foundation: `venture`, append-only mandate versions, `action_request`
intake, and append-only investment decisions. This ADR records the durable
decisions those primitives lock in. It does not reopen the frozen architecture.

## Decisions

### Three status concepts are separate — enforced by the schema

- **Venture lifecycle state** (`venture.lifecycle_state`) — where a venture is
  in its life.
- **Run/task status** (`action_request.status`) — status of a unit of work.
- **Investment decision** (`investment_decision_record.decision`) — an
  allocation decision/outcome.

They are three distinct PostgreSQL ENUM types (from Slice 1). Each column
accepts only its own vocabulary and rejects the others', so the concepts can
never collapse into one generic `status` field. Recording a decision (e.g.
`KILL`, `SCALE`) never mutates lifecycle state.

### ActionRequest idempotency

Intake is keyed by `(venture_id, idempotency_key)` with a database `UNIQUE`
constraint and `INSERT ... ON CONFLICT DO NOTHING` — never an in-memory
pre-check.

- First request → exactly one ActionRequest + one creation audit event.
- Exact duplicate (same canonical payload) → returns the existing request; no
  second row, no second creation event.
- **Same key + different canonical payload → hard, deterministic conflict**
  (`IdempotencyConflictError`). The earlier request is never silently returned;
  this prevents accidental or malicious semantic reuse of a key.
- Concurrent duplicate inserts converge on one canonical row (verified by a
  multi-connection test).

Payloads are compared by a canonical SHA-256 hash computed from key-sorted,
compact JSON, so logically identical objects with different key ordering do not
produce a false conflict. The hash is for idempotency/provenance only — it is
not a claim of evidence truth.

### Guarded lifecycle transitions

There is exactly one canonical path to change lifecycle state: a guarded
`transition()` function that checks an explicit permitted-transition set under a
row lock and writes the change and its audit event in the same transaction.
There is no generic "set lifecycle_state" method. Illegal transitions raise and
leave lifecycle and audit history unchanged.

### Mandate versions are append-only

`venture_mandate_version` rows are immutable at the database level (UPDATE,
DELETE and TRUNCATE are rejected by triggers) and unique per
`(venture_id, version)`. AIDAN cannot overwrite an existing version. Gate 1
stores only the durable reference primitive (version + content hash +
optional source ref); mandate authoring, generation and interpretation are out
of scope.

### PostgreSQL is authoritative

All domain objects are projections of PostgreSQL state. No SQLite, file, cache,
queue or in-memory canonical store is introduced. Every canonical write in this
slice emits an audit event in the same transaction as the state change.

## Consequences

- Later slices add Policy, Approvals, Budget, Proof Receipts, Evidence and Kill
  Switch on top of these primitives; none are implemented here.
- The audit event taxonomy is deliberately small: `venture.created`,
  `venture.mandate_version_appended`, `action_request.created`,
  `venture.lifecycle_transition`, `investment_decision.recorded`.
