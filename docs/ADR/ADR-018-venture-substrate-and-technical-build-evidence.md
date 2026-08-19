# ADR-018 — Venture Substrate & Technical Build Evidence

**Status:** Accepted
**Gate:** 5 — Venture Substrate + Builder Quality (Slice 2)

## Context

Slice 1 established the immutable BUILD authority boundary (build_spec, venture
repository, execution-spec binding). Slice 2 adds the smallest durable evidence
that answers: what reusable infrastructure did a build start from, what candidate
did the builder produce, which files belong to that attempt, and did the candidate
pass deterministic Technical checks — all reconstructable from PostgreSQL without
trusting Builder prose. It deliberately does NOT judge Product, Experience,
Commercial, or AntiGeneric quality (Slice 3), and adds no deployment.

## Decisions

### The substrate is infrastructure, not a product template

The first Venture Substrate ships exactly two genuinely repeated infrastructure
components — `CONFIG_BOUNDARY` (environment/secret-exclusion convention) and
`TEST_HARNESS` (test-command + machine success rule) — as real source under the OS
monorepo `substrate/` tree. Both are reusable across materially different ventures,
are deterministic and bounded, and constrain no product design. A deterministic
scope guard (`assert_substrate_scope`) rejects any substrate file that looks like
product-template material (dashboard, navigation, pricing, onboarding, chat UI,
brand/copy) or is not an allowed infrastructure content type. That is
substrate-scope validation, **not** the Slice 3 AntiGeneric product decision.
Deferred by design: auth UI, app shell, design system, persistence model,
telemetry product UI.

### substrate_release is real provenance, not a version string

`substrate_release` freezes the exact OS `source_sha`, the finite selected
components, and a deterministic `content_hash` computed over the ACTUAL component
file hashes. Identity is immutable and idempotent: an identical release converges;
a changed source SHA, component selection, or component content conflicts. A bare
`version="v1"` is never sufficient.

### The venture workspace is root-contained, not a code sandbox

A workspace is a disposable directory tree; file materialization (substrate first,
then candidate files) is root-contained — absolute paths, `..` traversal, and
symlink escapes are rejected, and the canonical OS repository tree can never be a
workspace. The honest claim is "file materialization is root-contained to the
venture workspace", **not** "arbitrary untrusted code is sandboxed". Slice 2
executes NO arbitrary Builder code on the host: the `TEST_COMMAND` check is an
allowlist/structural check of the declared command, and real sandboxed execution is
deferred.

### build_manifest is kernel-derived candidate provenance, not a verdict

`build_manifest` records, per exact execution attempt, the materialized candidate
tree with KERNEL-COMPUTED hashes — the kernel writes and re-hashes the actual bytes,
so a worker cannot forge file identity (a worker-declared hash is ignored). It is
immutable and one-per-attempt; identical re-capture converges, any materially
changed candidate/substrate/binding conflicts, and composite FKs bind it to the same
venture, action, attempt, build_spec, repository, and substrate_release. Retries
never overwrite earlier build history.

### Technical quality is deterministic, derived, and worker-independent

`build_technical_check` persists per-check PASS/FAIL evidence for a finite check set
(CONTRACT_SCHEMA, WORKSPACE_CONTAINMENT, MANIFEST_INTEGRITY, SUBSTRATE_PROVENANCE,
REQUIRED_OUTPUT, FORBIDDEN_SECRET_FILE, TEST_COMMAND). The Technical verdict is
DERIVED (any required FAIL ⇒ FAIL) — there is no scalar score and one passing check
cannot compensate for another failing one. Worker/model self-reports
(`tests_passed`, `quality_pass`, …) are inert. This is a narrowly-scoped
build-Technical evidence store, deliberately NOT the Slice 3 multi-dimension quality
table.

### Execution SUCCESS ≠ Technical PASS; Technical evidence ≠ Gate 4 proof

A Gate 4 worker proof attests the bounded execution result; it does not certify
product Technical quality. A build can reach Gate 4 `SUCCEEDED` and still be
Technical FAIL. Slice 2 records Technical evidence only — it creates no proof, sets
no lifecycle, performs no merge/deploy, and makes no canonical BUILD success. The
final multi-quality consequential decision is a Slice 3 concern.

## Consequences

- Migration `0016` adds `substrate_release`, `build_manifest`, and
  `build_technical_check`; migrations `0001–0015` unchanged. No Product/Experience/
  Commercial/AntiGeneric tables, no aggregate score, no deployment.
- New production modules `aidan_core/build/{substrate,manifest,technical,workspace}.py`
  and `capture_and_check_build` composition; the Builder remains an ordinary Gate 4
  `WorkerAdapter` (no second runtime).
- Real infrastructure source lives under `substrate/` in the OS monorepo; only
  allowlisted component paths are materialized into a venture workspace.
- Dependencies: none (stdlib only). No arbitrary Builder code executes on the host.
