# ADR-005 — Policy, Kill Switch & Budget Controls

**Status:** Accepted
**Gate:** 1 — Truth & Governance (Slice 3)

## Context

Slice 3 adds the deterministic governance layer on the Slice 1–2 foundation:
a Policy Engine, kill switch, and budget/capital controls. This ADR records the
durable decisions. It does not reopen the frozen architecture.

## Decisions

### Deterministic policy, fixed precedence, no DSL

The Policy Engine is a pure function with a single fixed precedence:

1. GLOBAL kill switch active → `DENY`
2. VENTURE kill switch active → `DENY`
3. insufficient available budget → `DENY`
4. autonomy level insufficient → `REQUIRE_APPROVAL`
5. amount above the approval threshold → `REQUIRE_APPROVAL`
6. otherwise → `ALLOW`

Same canonical inputs always yield the same decision and the same
`inputs_hash`. There is no policy DSL, no database-driven rule scripting, and no
LLM. The only way to persist a decision is `evaluate_and_persist`, which derives
inputs from canonical state and computes the outcome itself; there is no API to
store a caller-supplied outcome. Policy decisions are append-only (immutable at
the DB level) and carry rule id/version, inputs hash and a JSON inputs snapshot
so a decision can be explained later.

### Kill-switch priority

Kill switch has GLOBAL (singleton) and VENTURE scopes; global takes precedence.
Invalid scope/venture combinations are rejected by the database (CHECK plus
partial unique indexes). Engage/release are transactional, idempotent, and
audited with who/why/when.

### Approval is a policy outcome; approval *execution* is Slice 4

`REQUIRE_APPROVAL` is a policy result only. Creating, waiting on, and resolving
approval records is deliberately out of scope for Slice 3.

### PostgreSQL-enforced budget reservation

Budget correctness is enforced by the database, not application code alone:

- fixed-precision `numeric` amounts (no floating point);
- `CHECK (reserved + committed <= granted)` and non-negative CHECKs;
- per-account row locking (`FOR UPDATE`) serialises concurrent reservations so
  they cannot overspend;
- partial unique indexes enforce **one RESERVE / RELEASE / COMMIT per action**;
- composite foreign keys enforce currency and venture consistency between the
  ledger, the account, and the action.

### Append-only capital ledger; idempotent transitions

`capital_entry` is append-only (immutable at the DB level). `GRANT`, `RESERVE`,
`RELEASE` and `COMMIT` are recorded as ledger entries. Reservation, release and
commit are idempotent; release-after-commit and commit-after-release are
rejected. Actual-cost reconciliation is out of scope (Slice 4).

### Governance decides only

Slice 3 governance never mutates lifecycle state, investment decisions, or
ActionRequest status, and never executes an action.

## Consequences

- Slice 4 adds approvals, execution binding, proof receipts and reconciliation
  on top of these primitives.
- Audit taxonomy grows by: `policy.evaluated`, `killswitch.engaged`,
  `killswitch.released`, `budget.granted`, `budget.reserved`,
  `budget.released`, `budget.committed`.
