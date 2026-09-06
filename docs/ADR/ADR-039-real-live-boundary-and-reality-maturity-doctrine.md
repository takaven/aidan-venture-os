# ADR-039 — Real Live-Boundary and Reality-Maturity Doctrine

**Status:** Accepted
**Gate:** cross-cutting (all real external boundaries)

## Context

The programme proves consequential capability by exercising real external systems (research provider,
coding worker, deployment provider, market channel). Two failure modes must be guarded against:
(1) treating an **isolated live smoke** as if it were the finished product, and (2) after a live
failure, "buying information one live consequential action at a time" instead of reproducing the
failure deterministically first. The Postmark market-ingress sequence (documented in
`docs/PROGRAMME_STATUS.md`) exhibited both and is the motivating experience for this ADR.

## Decision

**Reality-maturity ladder.** Consequential capability matures along:

```
DETERMINISTIC → CONTRACT-PROVEN → LIVE-SMOKED → COMPOSED-REAL → QUALIFYING-REAL
```

- A **LIVE-SMOKED** boundary proves one real edge works once. It is **not** the product.
- **COMPOSED-REAL** (multiple real boundaries composed into one governed venture loop) and
  **QUALIFYING-REAL** (outcomes that move portfolio value) are the actual objectives.

**Live-boundary discipline.** Every consequential live action requires explicit bounded authorization
(one action, one target/recipient, a spend ceiling, no retry). After a consequential live **failure**,
before any new consequential attempt:

1. Preserve **complete** terminal verifier/proof evidence (do not let per-check detail vanish with an
   ephemeral run).
2. Validate **every** provider-dependent configuration field.
3. Reproduce the failure **deterministically** in fake/contract tests.
4. Close the defect in fake/contract tests.
5. Then perform **at most one** newly justified live boundary.

**Verification precedence.** Deterministic provider/system evidence outranks agent self-report at
every rung; partial or subset diagnostic evidence must never be treated as full-verifier equivalence.

## Consequences

- Diagnostics must be explicit about which authoritative fields they do and do not check, and live
  evidence must surface **all** verifier PASS/FAIL checks.
- Isolated boundary proofs (research, coding worker, deployment) are recorded as milestones but do not
  by themselves close a gate that requires composition.
- The programme prioritizes reaching a thin **COMPOSED-REAL** venture loop over accumulating further
  isolated boundary smokes.
