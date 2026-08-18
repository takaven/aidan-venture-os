# ADR-014 — Execution Spec & Worker Boundary

**Status:** Accepted
**Gate:** 4 — Durable Execution Runtime (Slice 1)

## Context

Gate 4 establishes the durable execution runtime — Factory. Factory is a durable
execution runtime, NOT an AI programmer: AIDAN remains the executive/capital
allocator, and workers are replaceable specialists behind a typed adapter. Gate 1
already provides the durable execution substrate (`execution_attempt`,
`execution_result`, `proof_receipt`, `recovery`, extended `run_status`). Slice 1
adds only the missing worker-dispatch boundary and closes one carried gap. This
ADR records the durable decisions; it does not reopen the frozen architecture.

## Decisions

### ActionRequest stays the sole authority; execution_spec is downstream detail

The canonical ActionRequest remains the only action authority. `execution_spec`
is a new 1:1, immutable record that freezes exactly what executable work,
capability boundary and future verification contract are bound to a governed
ActionRequest. It is not an investment decision, a second action, a lifecycle
state, or authorization by itself. No second action/job/run system is introduced;
the existing `execution_attempt` is the durable run primitive.

### The spec is immutable and deterministically identified

All executable-authority fields (worker kind, task payload, expected output
contract, verifier kind, timeout, max attempts, capabilities) are immutable at
the DB level; correcting a task means a new ActionRequest/spec, never a mutation.
`spec_hash` is a deterministic digest over those fields (capabilities sorted;
timestamps/ids excluded), so the executable authority has a stable identity.

### Authorization must apply to the frozen spec

A Policy decision or Approval created before the spec was frozen cannot authorize
that spec's dispatch (Gate 3 can policy-evaluate an ActionRequest before any spec
exists). The runtime therefore re-authorizes at dispatch against the frozen spec:
it performs a fresh policy evaluation (inherently post-spec) and, when approval is
required, demands a valid APPROVED approval **requested after the spec existed**.
Pre-spec authorization is ineligible; fresh, spec-aware authorization is required.
This reuses the Gate 1 policy/approval primitives — no parallel engine.

### Typed, provider-neutral, DB-authority-free workers

Workers implement `WorkerAdapter.execute(WorkerRequest) -> WorkerResult` and are
dispatched via a small registry keyed by `worker_kind`. A worker receives only
bounded execution data — never a database connection or credential — and returns
a `WorkerResult` that is a **claim and result data only**. Provider identity is
recorded as `worker_kind`/`worker_version` provenance and never defines
semantics. Worker output cannot broaden capabilities, change the verifier, mutate
the spec/policy/approval/lifecycle, or set canonical success — such content is
inert result data.

### Canonical SUCCESS is DB-enforced

Migration 0012 adds a trigger: an ActionRequest cannot transition to `SUCCEEDED`
without a VERIFIED Proof Receipt for that action. This closes the carried Gate
1/Gate 3 note that a raw `UPDATE ... SUCCEEDED` could bypass proof gating. The
canonical path is unaffected because it derives/records a VERIFIED proof before
transitioning, in the same transaction. (A prior Gate 1 test that simulated the
forced-SUCCEEDED corruption now asserts the DB rejects it — a strictly stronger
guarantee.)

### DB vs kernel enforcement (Slice 1)

- **DB-enforced:** spec 1:1; venture consistency (composite FK); spec
  immutability; capability vocabulary; SUCCEEDED-requires-VERIFIED-proof; existing
  attempt/result/proof/idempotency constraints.
- **Kernel-enforced:** worker registry/dispatch; capability presentation; no DB
  connection to workers; workspace binding; authorization freshness against the
  frozen spec.

## Consequences

- Migrations `0001–0011` remain unchanged; `0012` adds only `execution_spec` and
  the success guard (no failure taxonomy, no artifacts, no verifier-result table,
  no retry fields, no second run table — those are later slices).
- Slice 1 performs no verification, Proof Receipt creation, canonical SUCCESS,
  retries, timeouts, or recovery rewrite; a Gate 4 ActionRequest may be dispatched
  and captured without ever succeeding — that is correct. External exactly-once
  execution is not claimed.
- Gate 4 remains open; later slices add the deterministic verifier + Proof Receipt
  integration (Slice 2), retry/timeout/kill/recovery (Slice 3), and Track-A evals
  (Slice 4).
