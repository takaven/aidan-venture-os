# ADR-003 — Gate 1 PostgreSQL Foundation

**Status:** Accepted
**Gate:** 1 — Truth & Governance (Slice 1)

## Context

Gate 0 preserved and canonicalised the repository with no implementation. Gate 1
begins durable canonical truth. This ADR records the foundational engineering
decisions introduced by Gate 1 / Slice 1. It does not reopen the frozen
architecture.

## Decisions

### Stack

- **Python 3.12**, type hints, standard library first.
- **PostgreSQL 16** is the canonical durable state store.
- **psycopg 3** driver; **no ORM** and **no Alembic**. Canonical invariants live
  in the database (constraints, enum types, triggers), not in an object layer.
- **Forward-only, numbered SQL migrations** applied by a small deterministic
  runner.
- **pytest** for tests.
- **Pydantic v2** is part of the approved stack but is **not introduced in
  Slice 1**: this slice has no external/input DTO boundary that needs it. It
  will be added when the first genuine input boundary (ActionRequest payloads)
  arrives. Smallest dependency set: `psycopg[binary]` at runtime, `pytest` for
  tests.

### Migration doctrine

- Deterministic order by numeric filename prefix (`NNNN_name.sql`).
- Each applied migration is recorded in `schema_migrations` with an immutable
  SHA-256 **checksum**.
- An already-applied migration whose file has changed is a **hard failure**
  (checksum drift), never a silent re-apply.
- A migration and its `schema_migrations` record commit in the **same
  transaction**, so a failed migration is never recorded as applied and leaves
  no partial objects.
- **No downgrades.** Applied migrations are never edited.

### Canonical database role

PostgreSQL is the single canonical durable programme state. No competing truth
store is permitted. SQLite persistence patterns from donor repositories are
rejected for this reason (consistent with the salvage manifest).

### Vocabulary separation

Three status concepts are represented as **three distinct PostgreSQL ENUM
types** — `lifecycle_state`, `run_status`, `investment_decision` — mirrored by
three Python enums. Distinct DB types are the enforcement: a label valid for one
type is rejected by another. This prevents the concepts from ever collapsing
into one generic `status` field.

### Audit immutability

`audit_event` is append-only, enforced at the **database level** by triggers
that reject `UPDATE`, `DELETE` and `TRUNCATE`. Gate 1 deliberately does **not**
implement a cryptographic hash chain; plain append-only immutability is the
requirement, and prestige tamper-evidence is out of scope until a concrete need
is demonstrated.

### Exactly-once boundary (correction adopted from preflight review)

Gate 1 guarantees **exactly-once canonical completion** — canonical state
transitions and their records commit atomically and idempotently. It does
**not** claim universal exactly-once external side effects. Exactly-once
*external* effects require the executor to provide idempotency and/or
reconciliation capability; that requirement is deferred to the slices that
introduce execution.

### Gate 0 CI retirement / Gate 1 CI activation

The Gate 0 integrity workflow (`gate0-integrity.yml`) intentionally rejected any
implementation file under the reserved directories. Gate 0 is closed and this
prohibition is now obsolete. Its active workflow has been **removed** and
replaced by `gate1.yml`, which runs against a real `postgres:16` service
(install, migration bootstrap, pytest, `git diff --check`). Gate 0 evidence is
**preserved in Git history** and in `docs/GATE_0_EXECUTION_RECORD.md`; the
historical `tests/gate0_integrity.py` remains in the tree as a record but is no
longer an active required check. Two contradictory required workflows are not
kept.

## Consequences

- Later slices build ActionRequest, Policy, Approvals, Budget, Proof Receipts,
  Evidence and Kill Switch on this foundation; none are implemented here.
- Donor `AI-DAN-FRAMEWORK` (`core/operations.py`, MIT) remains **REIMPLEMENT /
  concept-only** — no code was copied.
- CI now depends on a PostgreSQL service; there is no local Docker requirement
  for contributors (tests skip cleanly without `DATABASE_URL`).
