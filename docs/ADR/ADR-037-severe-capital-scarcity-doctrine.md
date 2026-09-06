# ADR-037 — Severe Capital-Scarcity Operating Doctrine

**Status:** Accepted
**Gate:** cross-cutting (Governance / Intelligence)

## Context

AI-DAN's north star is improved long-term portfolio value through evidence-backed capital allocation.
A recurring failure mode for autonomous "venture builder" systems is to assume abundant capital and
optimize for products built or launched. AIDAN must instead be genuinely useful when capital is
severely scarce, where the dominant question is not "what can we build" but "what is the cheapest
action that would materially change a capital decision".

## Decision

AIDAN operates under a **severe capital-scarcity doctrine**:

- It preferentially discovers the **cheapest action capable of materially changing a capital
  decision**.
- Its optimization target is **decision value / uncertainty reduction per dollar**, not throughput of
  products.
- Realistic capital frames are on the order of **$20 / $100 / $300 / $500 / $1,000 / $2,000** — AIDAN
  must **not** be framed around requiring tens of thousands of dollars.
- **Weak ventures should die cheaply.** A fast, low-cost kill is a first-class successful outcome, not
  a failure.

## Consequences

- Kill Cases, cheap validation experiments, and small precommitted spend ceilings are the normal
  instruments; large speculative builds are the exception requiring strong evidence.
- Capital governance (reserve → reconcile, conservative cost on unknown) is doctrinally aligned with
  scarcity: unknown outcomes are costed conservatively and never optimistically.
- Evaluation of AIDAN's judgment must reward correct *cheap* kills and correct *small* bets, judged
  against evidence and outcomes — not document or infrastructure completeness.
