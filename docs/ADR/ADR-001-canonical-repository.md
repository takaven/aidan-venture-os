# ADR-001 — Canonical Repository

**Status:** Accepted
**Gate:** 0 — Preserve & Canonicalise

## Decision

The canonical implementation is a fresh repository named `aidan-venture-os`, with the intended private GitHub target `takaven/aidan-venture-os`.

Historical repositories do not become canonical.

The first Gate 0 execution produced transient commits `3d03c6f9f53da21a124054ba0abf11ef2b311c63` and `4a97ec96c83bd0ba30251f5659eae242ca637861`, but their Git object database was not preserved before sandbox loss. Those SHAs remain historical execution evidence but are not recoverable canonical history.

This ADR establishes the replacement recoverable Gate 0 Git history created during the bounded preservation correction as the canonical history intended for unchanged push to `takaven/aidan-venture-os`.

## Consequence

No donor repository is promoted wholesale. The transient first-execution SHAs must never be represented as recovered commits. The replacement canonical history must be preserved in a verified Git bundle before remote completion.

Remote visibility, default branch, remote HEAD, repository settings and GitHub Actions execution remain deferred until the target repository is accessible.
