# ADR-017 — Build Authority & Venture Repository

**Status:** Accepted
**Gate:** 5 — Venture Substrate + Builder Quality (Slice 1)

## Context

Gate 4 closed with a durable, proof-gated machine-execution runtime. Entering
Gate 5, the canonical BUILD seam was thin: a governed Gate 3 BUILD decision
intakes an `action_request` whose payload carries only
`{recommendation_id, opportunity_id, decision}`, and `action_request.action_type`
is unconstrained free text. Nothing forced venture-specific product intent into
what a builder would receive, so a BUILD decision could reach a coding worker as
free-form prose and produce an untraceable, generic application in an ungoverned
repository. Slice 1 closes exactly that gap — and nothing more. It adds no
product-quality engines, no build manifest, no substrate implementation, no
deployment, and no lifecycle transition.

## Decisions

### A BUILD decision is not direct coding authority

Before any builder runs, an immutable, venture-specific `build_spec` must be
frozen. It freezes the product intent and build acceptance contract (buyer,
problem, value proposition, product category, primary workflow, differentiators,
required/excluded capabilities, experience principles, expected output contract)
that an already-governed BUILD decision permits workers to implement. It is 1:1
with the BUILD `action_request` (`UNIQUE(action_request_id)`) and fully immutable
(append-only guard): correcting product intent requires a new governed BUILD
decision / ActionRequest / build_spec, never a mutation.

### build_spec is specification, not evidence

The product-intent fields are frozen REQUIREMENTS/decisions, not
SOURCE-CONFIRMED market facts, and worker-authored prose can never become
canonical commercial truth. Commercial/market evidence stays in the canonical
Gate 2/3 records the build_spec references and is never duplicated here.

### Concrete Gate 3 provenance — no polymorphic reference

Provenance is expressed with CONCRETE, venture-consistent composite foreign keys
to the actual canonical objects: the BUILD `investment_decision_record`, its
`next_action_recommendation` basis, and the `opportunity`. There is no
"reference-anything" column. A kernel guard additionally verifies the referenced
investment decision is genuinely `BUILD`, resulted in this exact ActionRequest,
matches the recommendation basis, and that the opportunity matches the
recommendation — so a build_spec can only be frozen against a real BUILD
authority, not merely because an ActionRequest exists or its text says "build".
This required one additive constraint, `investment_decision_record
UNIQUE(id, venture_id)` (a composite-FK target); migrations 0001–0014 are
untouched.

### Idempotency detects changed intent

Freezing mirrors the Gate 4 execution_spec rule: the same BUILD ActionRequest
with identical normalized content converges on the existing row; ANY material
change of product intent or authorizing provenance is a hard
`IdempotencyConflictError` — never a silent mutation (the row is immutable) and
never a silent return of a stale row under `UNIQUE(action_request_id)`. Identity
is a deterministic `spec_hash` over all material build authority (capability sets
sorted; narrative order preserved); DB-generated ids/timestamps are excluded.

### One isolated venture repository; the OS repo is never a target

A `venture_repository` names the isolated source-control boundary for one
venture's product: one canonical repository per venture (`UNIQUE(venture_id)`),
each repository backing at most one venture (`UNIQUE(repository_ref)`), immutable
registration (no silent reassignment). A builder may NEVER target the canonical
OS monorepo; this is enforced in trusted kernel code
(`assert_isolated_repository_ref`) rather than by a DB string CHECK, which could
only PRETEND that an opaque ref identifies the OS repo. It is a guard against the
known OS-repo identifiers, not a claim of filesystem-level sandboxing (a later
concern). Slice 1 is identity/authority only: no GitHub provisioning, no clone or
worktree mechanics, no network.

### The Builder is a Gate 4 WorkerAdapter — no second runtime

There is no `BuilderAdapter`, no `BuilderRegistry`, and no separate
dispatch/verify/retry loop. A builder is an ordinary `WorkerAdapter`. Build
dispatch composes the frozen authority onto the EXISTING Gate 4 path: it validates
build authority, freezes the immutable `execution_spec` whose `task_payload` binds
the build_spec identity+hash, the venture repository, and the venture-specific
intent, and dispatches through `factory.runtime.execute_action`. The
builder-specific typed contract (`BuildInput`) travels INSIDE the canonical
`WorkerRequest.task_payload`; the isolated repository is the `workspace_ref`.
Because the execution_spec's task payload embeds the build_spec hash, a materially
changed build intent (a different build_spec) can never mutate an existing
execution spec — it is a hard conflict, exactly as Gate 4 requires.

### Dispatch authorization is fresh and post-spec

The historical Gate 3 Policy decision predates the build spec and cannot authorize
builder dispatch. Authorization is obtained by the Gate 4 runtime only AFTER the
build intent is frozen into the immutable execution spec: the existing Gate 1
Policy/Approval primitives are reused (no second governance engine), and an
approval requested before the execution spec was frozen is ineligible.

### Worker result is a claim only

Slice 1 stops at result capture. A builder's self-report — including any claimed
`quality_pass`, `lifecycle`, `merge`, `deploy`, or attempts to broaden
capabilities or rewrite its own build_spec — is inert `WorkerResult` data. No
quality PASS, no proof, no lifecycle transition, no merge, and no deployment occur.

### Substrate identity is deferred (Option B)

No Venture Substrate exists yet, so binding a substrate release identity now would
record provenance (a version/source SHA) for a substrate that does not exist —
provenance theater. `build_spec` therefore carries NO substrate field and the
`spec_hash` excludes substrate. A minimal immutable `substrate_release` identity
(with a real source SHA) and its binding into build_spec/execution_spec are
deferred to Slice 2, when the substrate actually exists.

## Consequences

- Migration `0015` adds `build_spec`, `venture_repository`, and the single
  additive `investment_decision_record UNIQUE(id, venture_id)`; migrations
  `0001–0014` are unchanged. No build manifest, quality, substrate, or deployment
  tables.
- New production package `aidan_core/build/` (`spec`, `repository`, `runtime`).
  A `.gitignore` negation was required because the repo ignores `build/` as a
  build-artifact directory; the source package (its `*.py`) is now tracked while
  `__pycache__/*.pyc` remain ignored.
- One new error class, `BuildAuthorityError`.
- Quality dimensions, the anti-generic gate, build manifest, and substrate
  implementation are Slice 2+/later-gate concerns and are explicitly absent here.
