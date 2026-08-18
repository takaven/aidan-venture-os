# ADR-006 — Approvals, Proof & Recovery

**Status:** Accepted
**Gate:** 1 — Truth & Governance (Slice 4, final)

## Context

Slice 4 completes the Gate 1 governed consequential-action loop: approvals,
Evidence Ledger primitive, raw execution results, deterministic Proof Receipts,
guarded execution authorization/claiming, execution leases with safety modes,
crash recovery, and actual-cost reconciliation. This ADR records the durable
decisions; it does not reopen the frozen architecture and it does not implement
a Gate 4 worker runtime.

## Decisions

### An approval binds to an exact policy state

An approval is created for a specific `policy_decision` and its
`bound_inputs_hash`. At execution time the policy is re-evaluated; the approval
authorises execution only if it is `APPROVED`, not past `expires_at`, and its
bound hash equals the *current* inputs hash. If any policy input changed
(kill switch, budget, autonomy, threshold), the approval is **stale** and does
not authorise; a fresh approval is required. `APPROVED`/`REJECTED`/`EXPIRED` are
terminal (DB-enforced); an expired approval cannot authorise even if the row
still reads `APPROVED`. Policy immutability is preserved — staleness is detected
by comparing the deterministic inputs hash, never by mutating prior records.

### Raw executor result ≠ Proof Receipt

A raw `execution_result` (append-only, deduped per `(action, external_result_id)`)
records what an executor reported. It never establishes canonical success. Only
a deterministic verifier produces a `VERIFIED` `proof_receipt`; a caller cannot
persist `result='VERIFIED'` directly. Canonical success requires a `VERIFIED`
receipt, and at most one exists per action (partial unique index).

### Exactly-once canonical completion vs conditional external exactly-once

Gate 1 guarantees **exactly-once canonical completion**: the success transaction
is atomic (accept/dedup result → verify → record proof → transition status →
reconcile budget → optional authorized lifecycle transition → audit), and the
`VERIFIED`-proof uniqueness plus guarded status transitions make it converge
once under duplicate callbacks and concurrency. Exactly-once **external** effect
is *not* claimed universally — it is credible only when the executor provides a
stable idempotency key or deterministic reconciliation.

### Stable execution key and safety modes

Each consequential action has a stable `execution_key` (`exec:<action_id>`)
shared across retries; attempts increment `attempt_number`, and at most one
`CLAIMED` attempt exists per action (partial unique index → concurrency-safe
claim). Recovery of an expired lease depends on `safety_mode`:

- **IDEMPOTENT** — reclaim safely (same execution key).
- **RECONCILABLE** — consult a deterministic reconciler; if the external effect
  already happened, record its result so completion can proceed; else reclaim.
- **UNSAFE** — never auto-retry an ambiguous attempt; move to a durable
  `RECOVERY_REQUIRED` state.

If a raw result already exists (crash after the external effect), recovery
surfaces it so the normal completion path runs once.

### Actual-cost reconciliation

Reservation is taken at claim time (not while waiting for approval). On success
the reservation is reconciled: the actual cost is committed and any unused
reservation released. Actual cost above the reservation consumes additional
available budget only if it fits; otherwise completion is rejected rather than
overspend (DB `CHECK` is the backstop). Duplicate completion does not double
charge. Actual cost is recorded as the COMMIT ledger amount; a separate `ACTUAL`
entry type was not needed for Gate 1, so `0003` was left untouched.

### No worker/runtime platform

This slice implements only the durable canonical protocol around a
simulated/replaceable executor. No worker orchestration, queues, provider
adapters, APIs or deployment infrastructure are introduced.

## Consequences

- Gate 1 is functionally complete: a simulated venture survives restart,
  retries, duplicate execution, approval waits and budget exhaustion without
  losing canonical truth or double-executing.
- Gate 4 will provide real executors that honour the safety-mode contract.
- Audit taxonomy grows by approval/execution/recovery/evidence events.
