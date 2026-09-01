# ADR-023 — Provider-Neutral REAL_EXTERNAL Deployment Contract

**Status:** Accepted
**Gate:** 6 — Verification & Deployment (real-deploy readiness)

## Context

Gate-6 Slices 1–3 (ADR-020/021/022) proved the full governed deployment chain — quality-qualified
`release_candidate` → venture-owned `deployment_target` → frozen deploy execution spec → worker
result as an inert CLAIM → forced `deployment-release` verification → the one `DEPLOYMENT_RELEASE`
Proof Receipt → proof-gated `BUILDING → OPERATING` — but deliberately against a controlled **LOCAL
target directory**. No external provider, network, credential, or consequential effect is involved
anywhere in the current subsystem (ADR-021: "proves architecture, not cloud").

This ADR freezes the **minimum contract** that extends that architecture to a genuine
`REAL_EXTERNAL — CONSEQUENTIAL DEPLOYMENT` **without weakening** immutable release authority, venture
target isolation, independent verification, Proof Receipt authority, capital governance, fail-closed
retry/recovery, or secret isolation. It selects **no provider** (none is canonically designated —
see "Provider state" below) and adds no provider-specific branching.

## What is already true (unchanged by this ADR)

- `WorkerResult` is a CLAIM; canonical deployment SUCCESS comes solely from the deterministic
  verifier. A worker's `deployed=true` claim is inert (ADR-021).
- The kernel — not the worker — determines the release bytes; the verifier re-hashes the observed
  tree and compares to the frozen `build_manifest.candidate_tree_hash` (independent, never copied).
- Consequential-effect semantics already exist generically at the Gate-4 runtime and a deploy
  worker inherits them: an `AmbiguousExternalEffectError` → `RECOVERY_REQUIRED` (not auto-claimable,
  never blind-retried); a `ProviderExecutionFailure` (known rejection) → terminal `FAILED`.
- `deployment_target` stores identity only and rejects secret-like keys (ADR-020).

## Decisions

### The read-back is a provider-neutral OBSERVER seam

Verification establishes observed deployment state through a `DeploymentObserver` that returns a
`DeploymentObservation` (isolation identity, observed release files + kernel hashes, health signal,
bounded contact evidence). The five required checks are pure functions over that observation, so
**local and real verification run identical logic**. The default `LocalTargetObserver` reproduces
Slices 1–3 exactly (every existing proof holds; the `DEPLOYMENT_RELEASE` `evidence_hash` formula is
unchanged). A real deployment injects a provider observer that reads the SAME observation shape back
from the external target. This is the sole architectural extension; nothing else in the chain moves.

### Minimum machine evidence a REAL_EXTERNAL deploy must independently establish

Frozen, provider-neutral. Every item is derived by the trusted kernel/verifier from an independent
read-back — never from the worker's self-report:

1. **Frozen release identity** — the exact `release_candidate_id` + `release_hash` +
   `candidate_tree_hash` bound into the immutable execution spec.
2. **Venture-owned target identity** — the exact `deployment_target` (id + environment + provider +
   opaque `target_ref`); the observed read-back target must resolve under this venture's + target's
   namespace (`VENTURE_TARGET_ISOLATION`), never another venture's.
3. **External deployment identity** — a durable, provider-issued external identifier for the
   deployment, captured as a claim and confirmed by the independent read-back.
4. **Provider contact / effect evidence** — bounded `OBSERVED | NOT_OBSERVED | UNKNOWN`; crossing a
   local process/transport boundary is NOT proof the external target was reached (mirrors the Codex
   provider-contact doctrine).
5. **Independent read-back** — the deployed tree re-hashed on the external target and compared to
   the frozen release identity (`RELEASE_IDENTITY`); wrong/extra/tampered bytes fail.
6. **Bounded health/runtime proof** — a bounded, deterministic health signal (`HEALTH`) and required
   runtime-contract artifacts present (`REQUIRED_RUNTIME_CONTRACT`); health is necessary, not
   sufficient (a healthy wrong release still fails), and exact bytes alone are insufficient.
7. **Durable external identifier** — persisted so the deployment is reconstructable from canonical
   state after the ephemeral run ends.

### Distinct signals (never conflated)

`local process/transport` ≠ `provider contact` ≠ `deployment effect` ≠ `independent verification`.
The Proof Receipt authority is `independent verification` alone; the other three are bounded evidence.

### Effect semantics (reused, not reinvented)

- Ambiguous consequential effect → `RECOVERY_REQUIRED` (explicit recovery only; no blind retry).
- Known rejection → `FAILED` (terminal).
- No VERIFIED `DEPLOYMENT_RELEASE` proof → no `OPERATING`. Kill switch rechecked at promotion.

### Governance & isolation (unchanged, restated as hard constraints)

- Bounded spend under the canonical capital ledger; explicit timeout; `max_attempts=1` for the first
  real smoke; exactly-once first-smoke semantics.
- No cross-venture target mutation. No worker self-certification.
- The credential is invocation-scoped (host-supplied to the worker's child only) and is **never**
  stored in `deployment_target` or any canonical row; sanitized evidence only (no raw
  transcript/stderr/secret).

## Stage-C first-smoke correction (honest scope)

The first real Fly smoke is a narrowly scoped **REAL_EXTERNAL — CONSEQUENTIAL DEPLOYMENT BOUNDARY
SMOKE**, not a production-operation proof. Corrections that bound it honestly:

- **Self-contained fixture.** The live smoke runs on a fresh ephemeral database, so it establishes
  its OWN complete bounded canonical fixture there (venture → quality-qualified build/release →
  venture-owned Fly `deployment_target` → deploy ActionRequest → frozen `release_candidate`) via the
  real guarded kernel APIs before any Fly mutation. It does NOT depend on a foreign ActionRequest id
  from another ephemeral run. Only the Fly app name + digest-pinned image + runtime/health contract
  are owner inputs (owner-created external infrastructure), frozen into `release_hash`.
- **Artifact claim narrowed.** The smoke proves only "AIDAN deployed the EXACT OCI artifact its
  frozen release authorized", NOT "the OCI artifact was built from `candidate_tree_hash`". The
  `candidate_tree_hash → OCI digest` derivation is a separate pinned host build tool, **UNPROVEN by
  this smoke** (`SOURCE_TO_ARTIFACT_DERIVATION_PROVEN = False`); a later composed production proof
  must close that bridge.
- **Runtime/network contract frozen.** The Machine service/port config (internal_port, ports,
  protocol) that makes `<app>.fly.dev` externally reachable is frozen into the immutable
  `release_contract` (`runtime_contract`); the worker emits it and never invents it, and refuses to
  create a machine without it. The app's public IP allocation for `<app>.fly.dev` is OWNER-ONLY
  setup (a separate Fly step from machine creation).
- **Ephemeral — no OPERATING promotion.** The smoke sequence is fixture → governed create →
  independent observe → deterministic verify → DEPLOYMENT_RELEASE proof → **governed cleanup**
  (delete exactly the created machine + independently confirm absence). It does NOT call
  `promote_verified_deployment`; the venture stays **BUILDING**. The proof is historical evidence the
  external boundary worked, not a claim a runtime remains operating. A PASS requires cleanup
  `CLEANUP_CONFIRMED`; an ambiguous cleanup is never a clean PASS (owner-actionable reconciliation is
  returned, and no second machine is ever created).

## Provider state (as of this ADR)

**CASE B — no external deployment provider is canonically designated.** Evidence: `provider_kind`
is an opaque adapter string with no external branch anywhere; all values in the repo are test labels
(`fake-a`, `fake-b`, `fleet`, `local`, `edge-runtime`, …); no Render/Fly/Vercel/Railway/etc. is named
in production code, docs, config, or workflows; the ADRs state "no external provider." This ADR
therefore selects none and freezes only the provider-neutral contract above.

## Consequences

- New production module `aidan_core/deploy/observe.py` (the provider-neutral observer seam);
  `deploy/checks.py` refactored to pure functions over a `DeploymentObservation`; the verifier gains
  an injectable `observer_factory` (default local). No behavior change on the proven local path.
- No migration; migrations remain `0001–0025`. No provider dependency, no network, no live deploy.
- The exact remaining infrastructure requirement to run a first real deploy smoke is external to the
  kernel: a designated venture-owned external deployment target + an invocation-scoped deploy
  credential + a provider read-back observer implementation. Until those exist, no real deploy is
  possible and none is prepared.
