# ADR-016 — Retry, Recovery & Reauthorization

**Status:** Accepted
**Gate:** 4 — Durable Execution Runtime (Slice 3)

## Context

Slices 1–2 established immutable specs, claim-only dispatch, and deterministic
verification → proof-gated completion. Slice 3 closes the durable machine-execution
control loop: bounded retries, timeouts, crash recovery, and reauthorization
between attempts. The load-bearing constraint is that the existing terminal-state
semantics (`FAILED` is terminal, unclaimable) must not be weakened to make retries
work. This ADR records the durable decisions; it does not reopen the architecture.

## Decisions

### A retry is a new attempt of the same immutable task

Retry never changes the task: the same `execution_spec` (same `spec_hash`,
`task_payload`, `capability_scope`, `verifier_kind`, contract) drives a new
`execution_attempt`. Correcting a task requires a new ActionRequest/spec.
`execution_spec.max_attempts` is the canonical retry budget; the attempt count is
derived from durable `execution_attempt` history, never from a caller override.

### ActionRequest failure ≠ attempt failure

A retryable attempt failure with attempts remaining is not action failure: the
attempt is marked `FAILED` with a deterministic `failure_class`, and the action
returns to the claimable `PENDING` state (a new `RUNNING→PENDING` transition),
while the single budget reservation is HELD across attempts. The ActionRequest
becomes terminal `FAILED` only on a non-retryable failure or retry exhaustion
(`RETRY_EXHAUSTED`), releasing the reservation. No second state machine is
introduced, and retry state never touches lifecycle/investment truth.

### Deterministic failure classification, kernel-owned

`failure_class` ∈ {WORKER_ERROR, TIMEOUT, VERIFICATION_FAILED, POLICY_REVOKED,
KILLED, RETRY_EXHAUSTED, RECOVERY_REQUIRED, INTERNAL_ERROR}. Retryable classes are
WORKER_ERROR, TIMEOUT and VERIFICATION_FAILED. A worker exception is WORKER_ERROR;
a run exceeding `execution_spec.timeout_seconds` (measured by an injectable
monotonic clock) is TIMEOUT; a verifier rejection is VERIFICATION_FAILED. The
worker's own `failure_metadata` is inert — the kernel determines the class.

### Reauthorization before every attempt

Every dispatch — first or retry — re-checks kill switch, policy, budget and
approval by reusing the existing spec-bound authorization gate and
`authorize_and_claim`. Stale authorization is never reused: a kill switch, a
policy DENY, an insufficient budget, or a missing/stale post-spec approval blocks
the retry with no worker dispatch. The Slice 1 approval chronology binding
remains in force.

### Timeout and late results

A timed-out attempt captures NO result, so a late/over-time result cannot become
canonical success for that attempt. Timeout is retryable while attempts remain.

### Recovery reuses existing primitives; durable-state resume

`resume_action` completes an attempt whose worker result was already durably
captured before a crash — it verifies from PostgreSQL alone and completes WITHOUT
re-dispatching the worker (crash-after-result window). Crashed attempts that never
persisted a result remain the province of the existing `recovery.recover_action`
safety-mode machinery (IDEMPOTENT reclaim, UNSAFE → RECOVERY_REQUIRED, no
auto-rerun). The crash-after-proof window does not exist: proof recording and the
success transition are one atomic transaction.

### Canonical completion semantics

External worker dispatch is at-least-once (a crash window may re-dispatch under a
safe safety mode); canonical completion is exactly-once (VERIFIED-proof partial
unique + proof-gated transition + the 0012 DB guard). Exactly-once external side
effects are NOT claimed; UNSAFE ambiguity requires governed recovery, not
automatic rerun.

## Consequences

- Migration `0014` adds only `execution_attempt.failure_class/failure_detail/
  finished_at`; migrations `0001–0013` unchanged. No scheduler/queue/job table, no
  lease redesign, no second run layer.
- Rejected proofs and all prior attempts/results/artifacts remain append-only;
  only one VERIFIED proof per action; a later attempt may produce it.
- Slice 4 owns the consolidated Track-A development matrix and the held-out
  execution eval set; no held-out fixtures are introduced here. Gate 4 remains
  open. No Gate 5 (product build/deploy) behavior is introduced.
