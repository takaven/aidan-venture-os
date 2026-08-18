# ADR-002 — Historical Repositories Are Donors

**Status:** Accepted
**Gate:** 0 — Preserve & Canonicalise

## Decision

Historical repositories are evidence and implementation donors only.

- No bulk merges.
- Every implementation salvage requires an exact source repository, commit SHA, path, branch/PR where relevant, licence/provenance status, explicit disposition, rationale, modifications and verification tests.
- Code with unresolved licensing may not enter the canonical implementation.
- Historical documentation or PR prose is not execution evidence.

## Dispositions

`PORT / ADAPT / REIMPLEMENT / CONCEPT_ONLY / REJECT`.
