# ADR-021 — Deterministic Deployment Verification

**Status:** Accepted
**Gate:** 6 — Verification & Deployment (Slice 2)

## Context

Slice 1 froze release authority (quality-qualified `release_candidate` + venture-owned
`deployment_target`) and the deploy execution-spec guard. Slice 2 lets a bound release
execute and obtain canonical deployment SUCCESS — only from trusted deterministic
verification, never from a worker's self-report.

## Decisions

### Worker success ≠ deployment success

A deploy worker is an ordinary Gate-4 `WorkerAdapter`. Its result (including any
`deployed`/`release_verified`/`health`/`lifecycle` claim) is inert. Canonical
deployment SUCCESS comes solely from `verify_deploy`, which runs a deterministic
verifier and completes through the existing proof-gated path. Execution SUCCESS and
deployment SUCCESS are independent: a worker can execute cleanly and still produce a
deployment that fails verification (no VERIFIED proof).

### The kernel — not the worker — determines what bytes the release is

`prepare_deploy_execution` reconstructs the exact frozen release bundle
deterministically from durable state (the build_manifest's substrate + candidate
files, carried as lossless latin-1 so hashes match across platforms) and freezes the
verification contract into the immutable execution spec. A compliant worker
materializes that bundle into a controlled, **venture-isolated local target**; a
worker cannot choose a different candidate/revision.

### Release identity is verified independently, not copied

The `DeploymentReleaseVerifier` re-hashes the ACTUAL deployed tree on the target and
compares it to the frozen release identity (`build_manifest.candidate_tree_hash`) — it
never trusts a worker-returned hash. Wrong bytes, an extra file, or post-deploy tamper
all change the observed hash and fail `RELEASE_IDENTITY`.

### The forced deployment verifier + five required checks

A deploy execution spec's `verifier_kind` is forced to `deployment-release`, so a deploy
action can never be completed by a worker-self-report verifier. Verification requires
ALL of `VENTURE_TARGET_ISOLATION`, `TARGET_EXISTS`, `RELEASE_IDENTITY`, `HEALTH`,
`REQUIRED_RUNTIME_CONTRACT` to pass — no score, no compensation. Health is a bounded
deterministic marker (no network, no arbitrary code execution); it is necessary but not
sufficient (a healthy wrong release still fails), and exact bytes alone are insufficient
(an unhealthy or contract-incomplete exact release still fails).

### The controlled local target proves architecture, not cloud

The target is a deterministic local directory (`<tmp>/aidan-deploy/<venture>/<target>`)
whose observed state the verifier reads back. It proves the release-identity/health/
runtime/isolation architecture — NOT live cloud deployment. No external provider,
network, credential, or arbitrary host-command execution is involved. Provider identity
is an adapter string; verification is provider-neutral.

### One proof system; no lifecycle change

A VERIFIED deployment produces the ONE canonical `proof_receipt`
(`verification_type=DEPLOYMENT_RELEASE`) bound to the exact deploy action + execution
attempt, reusing the Gate-4 exactly-once proof authority — there is no second proof or
verification table and no new migration. Deployment failure reuses Gate-4 retry
(same `release_candidate`, new attempt). Deployment failure never alters Gate-5 quality,
and a VERIFIED deployment does NOT transition lifecycle (`BUILDING→OPERATING`) or write
a `deployment_record` — both are deliberately deferred to Slice 3, proving that a
deployment proof is not a lifecycle mutation.

## Consequences

- New production modules `aidan_core/deploy/{checks,verifiers}.py` and additions to
  `deploy/runtime.py` (`verify_deploy`, bundle reconstruction, forced deploy verifier).
- No new migration; migrations remain `0001–0018`. No `deployment_record`, no lifecycle
  transition, no external provider, no dependencies.
- `prepare_deploy_execution`/`execute_deploy` no longer accept a `verifier_kind` (the
  deploy verifier is forced).
