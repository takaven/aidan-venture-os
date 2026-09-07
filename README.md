# AI-DAN Venture OS

AI-DAN Venture OS is the canonical engineering repository for a **governed autonomous venture
allocator/operator**. Its north star is **improved long-term portfolio value through evidence-backed
capital allocation and verified execution** — not the number of products built or launched. The
fundamental unit is the **highest-value next action**, not "build another product".

## Programme status (2026-09-07 — strategic pivot)

- **Architecture: FROZEN.** Changes require execution evidence that the frozen design cannot satisfy a
  current-gate requirement.
- **STRATEGIC PIVOT (binding): all infrastructure work is PARKED** — Postmark, Fly, Codex, Factory,
  Operate, Gate 9, Gate 10. The **only allowed work** is the **Phase-1 judgment evaluation harness**
  in [`evals/judgment/`](evals/judgment/README.md).
- **The existential question being tested:** does the **same frontier model**, routed through AIDAN's
  structured decision process (**T**), produce **materially better, cost-adjusted** venture decisions
  than (1) a strong general prompt (**C0**) and (2) a much simpler distilled-AIDAN prompt (**C1**)? If
  the structure adds no material lift over C1, the structure is not justified.
- **Foundations already built** (now parked context): a governance kernel with deterministic
  verification and Proof Receipts running in CI against real PostgreSQL, plus three isolated real
  external boundaries proven — **research** (Claude Sonnet + Tavily + Anthropic), **coding worker**
  (OpenAI Codex), **deployment** (Fly Machines). Market ingress (Postmark) reached a real send but
  **never passed the full MARKET_ACTION verifier** — parked, not complete.
- **First action on resumption:** build and run the blinded, same-model judgment evaluation harness in
  `evals/judgment/` (frozen design in its `README.md`). Do **not** resume parked infrastructure.

> ⚠️ **Do not rerun historical live workflows** (research/Codex/Fly/Postmark) to "confirm" past
> evidence, and **do not resume parked infrastructure** — see `docs/PROGRAMME_STATUS.md` §0 and §O.

## Where to start
- **`AGENTS.md`** — entry point and rules for any fresh execution/review agent.
- **`docs/PROGRAMME_STATUS.md`** — authoritative current state, the strategic pivot (§0), and restart plan.
- **`evals/judgment/README.md`** — frozen design of the only currently allowed work.
- **`docs/architecture/ARCHITECTURE.md`** — the frozen four-plane architecture and doctrine.
- **`docs/ADR/`** — accepted Architecture Decision Records.
- **`docs/GATE_0_EXECUTION_RECORD.md`**, **`docs/donor-provenance/`** — historical foundation (Gate 0).

Gate 0 (Preserve & Canonicalise) is complete and preserved as historical background; the first
transient Gate 0 Git object database was not preserved (see ADR-001 and the Gate 0 execution record).
