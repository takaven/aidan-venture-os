# ADR-010 — Mandate-Driven Research Orchestration

**Status:** Accepted
**Gate:** 2 — Autonomous Research (Slice 4, final)

## Context

Slice 4 adds the bounded autonomous research loop that turns a canonical Venture
Mandate into evidence-preserving research outcomes, coordinating the Slice 1–3
primitives. This ADR records the durable decisions; it does not reopen the frozen
architecture, and it does not close Gate 2 (a separate exit audit follows).

## Decisions

### Mandate is the only venture-specific starting input

`run_research` takes a `venture_id`, a canonical `mandate_version`, and the
Mandate content for the run. Before any research, it deterministically verifies
the supplied content hashes to the canonical `venture_mandate_version.content_hash`
and rejects a mismatch (`MandateMismatchError`). A `research_run` row references
the exact mandate version by composite FK. Research never rewrites its own
Mandate; injected source text cannot change it.

### Acquire data; propose reasoning; kernel persists

A provider-neutral `ResearchAdapter` acquires untrusted DATA only. A
provider-neutral `ResearchProposer` receives the Mandate and acquired sources as
data (by index + content only — never a DB connection, never canonical ids) and
returns typed proposals. Only the deterministic orchestration kernel performs
canonical writes. Agents propose; governed deterministic systems persist. No live
LLM/provider SDK was added — CI uses deterministic replay fixtures.

### No invented evidence (load-bearing)

During a run the exact acquired content is available ephemerally. A proposed
Observation is persisted only if its excerpt is an **exact substring** of the
exact acquired content whose hash backs its Source Receipt (the same `normalize`
representation is used for hashing and verification). Fabricated excerpts are
rejected, never become Observations, and never create SUPPORTS relations. This is
provenance/anchoring verification, not semantic-truth or fuzzy-similarity
checking, and no model is asked to vouch for its own quotation.

### Truth vs reasoning preserved end-to-end

SOURCE, OBSERVATION and CLAIM remain evidence; INTERPRETATION, ASSUMPTION,
OPPORTUNITY and KILL CASE remain non-evidence reasoning. A Claim is a proposition;
its state is derived from relations. Contradictory evidence is preserved (a
DISPUTED claim keeps both stances). Stale evidence is retained and remains
freshness-qualifiable, never deleted or rewritten.

### Candidate readiness is structural, plus a genuine supporting path

For the autonomous path an Opportunity is finalized CANDIDATE only if Slice 3's
structural guard passes (hypotheses, linked Assumption, complete Kill Case,
critical unknown) **and** at least one linked Claim has a genuine SUPPORTS path
(state SUPPORTED or DISPUTED). Unsupported or fabricated-only claims cannot make a
candidate. No numeric thresholds, no scores, no favourable-Kill-Case requirement.
CANDIDATE is Gate-2 research readiness, not BUILD or investment approval, and has
no capital/ActionRequest/lifecycle/investment side effect.

### Valid negative outcomes

Deterministic terminal outcome: `OPPORTUNITIES_FOUND` if ≥1 candidate;
`INSUFFICIENT_EVIDENCE` if no verified observations (sparse/failed acquisition);
otherwise `NO_CREDIBLE_OPPORTUNITY`. Zero candidates is a valid success case.
Provider/acquisition failure fabricates no replacement evidence.

### Bounded, idempotent, no future-gate machinery

A run is synchronous and bounded (a per-question acquisition cap prevents runaway
loops). It is idempotent: an exact retry converges via the lower-level idempotency
keys with no duplicate canonical rows or audit events; the same run key with a
different Mandate is a hard conflict. No experiment selection, cost-to-learn
ranking, capital request, investment decision, worker fleet, queue, scheduler or
DAG is introduced. Held-out fixtures prove invariant generalization, not model
intelligence, and involve no production case-specific branching.

## Consequences

- Gate 2 is functionally complete pending its independent exit audit.
- Migrations `0001–0007` remain unchanged; `0008` adds only `research_run` and
  `research_question`.
