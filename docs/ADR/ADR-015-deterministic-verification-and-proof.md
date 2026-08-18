# ADR-015 — Deterministic Verification & Proof

**Status:** Accepted
**Gate:** 4 — Durable Execution Runtime (Slice 2)

## Context

Slice 1 established immutable execution specs, a typed worker boundary, and
claim-only dispatch. Slice 2 completes the deterministic verification boundary:
worker outputs become durable artifacts, a deterministic verifier judges them
against the immutable contract, and only a VERIFIED result — expressed through the
existing canonical Proof Receipt — permits canonical SUCCESS. This ADR records the
durable decisions; it does not reopen the frozen architecture.

## Decisions

### Deterministic verification outranks worker self-report — in both directions

A `WorkerResult` is a claim. Canonical success is decided by a deterministic
verifier over canonical inputs, not by the worker's reported outcome: a worker
that claims success but fails verification does not succeed, and a worker that is
uncertain while deterministic evidence satisfies the contract may succeed.
Artifact existence is likewise never success.

### One canonical verification receipt; no second truth store

The existing `proof_receipt` remains the sole consequential verification receipt.
A verifier returns a transient `VerificationResult`; the trusted kernel converts a
VERIFIED result into the canonical receipt via the existing single receipt writer
(`proof._record_receipt`). No `factory_proof` / `verification_result` truth table
is introduced. The receipt gains an optional `execution_attempt_id` so a success
can name the attempt whose machine verification produced it, and a proof may cite
only an attempt of its own action (composite FK).

### The verdict is derived from the immutable spec, never caller-supplied

No public completion API accepts a verifier, a verdict, or a verifier kind.
`complete_execution` (public) is fixed to the built-in deterministic token
verifier and takes no verifier parameter. `verify_and_complete` resolves the
verifier solely from the IMMUTABLE `execution_spec.verifier_kind` via a trusted
registry, runs it over canonical inputs, and records the derived verdict through
`proof._write_receipt` — the single internal receipt writer — via the shared
private completion core `execution._complete`. There is no public seam through
which a caller or worker can inject verification code or a verdict. Moreover, an
action that carries an `execution_spec` cannot be completed through the token
path at all (the completion core rejects it), so the deterministic token cannot
bypass a spec's chosen verifier.

### Verifiers are deterministic, provider-neutral, and pure

Slice 2 ships a structured-contract verifier and an artifact-hash verifier, chosen
via a `verifier_kind → Verifier` registry (no marketplace, no provider branching).
Verifiers receive canonical data and no database connection; they cannot mutate
policy, approval, lifecycle, spec, or status. No LLM/subjective verifier
participates in consequential success.

### Artifacts are provenance; verification re-hashes durable content

`execution_artifact` is append-only, venture-consistent, and bound to exactly one
attempt (composite FKs); its content hash is computed by the kernel (a
worker-declared hash is never trusted or stored). Verification does not trust that
stored hash — a persisted hash cannot verify itself. The actual artifact content
survives durably in `execution_result.raw_payload`, so `verify_and_complete`
reconstructs verification inputs from PostgreSQL alone and the artifact-hash
verifier INDEPENDENTLY RE-HASHES the durable content against the immutable
expected hash. A fresh process can therefore re-verify a persisted attempt without
re-dispatching the worker — the persistence boundary Slice 3's recovery will rely
on. Artifact references are validated against path traversal; no filesystem
dereference is performed (refs are opaque identifiers over in-memory content), so
filesystem sandboxing is out of scope for this slice.

### The DB SUCCESS guard remains

The Slice 1 trigger (SUCCEEDED requires a VERIFIED proof) stays as
defense-in-depth; the Slice 2 success path satisfies it because the VERIFIED proof
is recorded before the transition in the same transaction.

## Consequences

- Migrations `0001–0012` unchanged; `0013` adds `execution_artifact`, a composite
  unique on `execution_attempt`, and a nullable `proof_receipt.execution_attempt_id`
  with an action-consistent FK. No retry/timeout/failure-taxonomy/queue/recovery
  schema.
- A rejected verification produces a canonical FAILED proof receipt and the
  existing non-success outcome; whether a rejection is retryable vs terminal is
  Slice 3's concern (retries, timeouts, kill/policy recheck, recovery).
- Deterministic/replay verification proves machine-execution correctness only —
  not commercial correctness, product quality, or deployment. Gate 4 remains open.
