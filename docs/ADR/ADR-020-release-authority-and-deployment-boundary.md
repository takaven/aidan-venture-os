# ADR-020 — Release Authority & Deployment Boundary

**Status:** Accepted
**Gate:** 6 — Verification & Deployment (Slice 1)

## Context

Gate 5 closed with a five-dimension build-quality decision. A Gate-5 overall PASS
is *quality* truth, not *deployment* authority. Gate 6 Slice 1 establishes the
authority boundary between a quality-passed candidate and any deployment execution,
without deploying, verifying, or transitioning lifecycle.

## Decisions

### A Gate-5 quality PASS is not deployment authority

Before a deploy worker may run, the exact quality-qualified candidate and the exact
deployment intent must be frozen in an immutable `release_candidate` (1:1 with the
deploy ActionRequest) bound to a venture-owned `deployment_target`. Overall Gate-5
quality (Technical + Product + Experience + Commercial + AntiGeneric) is a hard
prerequisite, **reloaded from PostgreSQL** for the exact `build_manifest` — never a
caller `quality_pass`, worker result, or reviewer observation. Each single dimension
FAIL blocks release creation.

### release_hash binds the complete release intent

`release_hash` is a deterministic identity over candidate identity (build manifest +
candidate tree hash + build spec) **and** target identity (id + environment +
provider + ref) **and** the normalized release contract — so a changed
target/environment/config can never masquerade as the same release. Identical
re-freeze converges; any material change is a hard `IdempotencyConflictError`. The
row is immutable.

### Deploy authority is enforced at the canonical execution-spec boundary

As with the Gate-5 BUILD guard, the authority lives at
`factory.spec.create_execution_spec`, not only in a deploy helper. A canonical deploy
action (`action_type='deploy'`, or any action that already owns a `release_candidate`)
may only get an execution spec when its task payload binds the exact release_candidate
id + release_hash + deployment_target — regardless of which trusted Factory caller
creates the spec. So a deploy action can never be executed from free-form intent, and
the worker can never choose what/where to deploy. Non-deploy actions are unaffected.

### Deployment target is identity, not secrets

`deployment_target` holds an opaque `target_ref` and a provider-neutral
`provider_kind` string; it stores **no** credential/secret (secret-like provenance
keys are rejected). Provider names are adapter identity, not architecture — there is
no provider-specific branching in release/factory code. Registration is immutable and
idempotent over all persisted fields.

### The deploy worker is a Gate-4 WorkerAdapter — no second runtime

There is no `DeployAdapter`/`DeployRegistry`. Deploy dispatch composes the frozen
release onto the existing Gate-4 path (`factory.runtime.execute_action`); the typed
`DeployInput` rides inside the canonical `WorkerRequest.task_payload`. Authorization
is obtained fresh and post-spec (Gate-4 chronology); pre-release/pre-spec approval is
ineligible.

### Slice 1 records no deployment success

Dispatch captures the worker result as a **claim only**. A worker's
`deployed`/`release_verified`/`lifecycle`/`overall_success` claims are inert. Slice 1
creates no deployment verification, no Proof Receipt, no `deployment_record`, and no
lifecycle transition; build-quality history is untouched. A Gate-4 worker-execution
proof (if produced) is not a deployment-success proof. Deterministic deployment
verification + proof are Slice 2; failure/retry/reconciliation + the governed
`BUILDING→OPERATING` transition are Slice 3.

## Consequences

- Migration `0018` adds `deployment_target` + `release_candidate` and extends the
  `execution_spec` capability CHECK with `DEPLOY_CANDIDATE`; migrations `0001–0017`
  unchanged. No deployment_record/proof/lifecycle schema.
- New production package `aidan_core/deploy/` (`target`, `release`, `runtime`); one
  new error `DeployAuthorityError`; `factory/spec.py` gains the deploy guard +
  `DEPLOY_CANDIDATE` in its capability vocabulary (mirrors the DB CHECK).
- Dependencies: none. No external provider, no network in Slice 1.
