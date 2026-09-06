# AI-DAN Venture OS

AI-DAN Venture OS is the canonical engineering repository for a **governed autonomous venture
allocator/operator**. Its north star is **improved long-term portfolio value through evidence-backed
capital allocation and verified execution** — not the number of products built or launched. The
fundamental unit is the **highest-value next action**, not "build another product".

## Programme status (2026-09-06)

- **Architecture: FROZEN.** Changes require execution evidence that the frozen design cannot satisfy a
  current-gate requirement.
- **Current gate: Gate 8 — active, not complete.** Governance kernel, deterministic verification, and
  Proof Receipts are implemented and running in CI against a real PostgreSQL.
- **Real external boundaries proven** (isolated live smokes):
  - **Research** — Claude Sonnet + Tavily + Anthropic (`FIRST REAL RESEARCH BOUNDARY PROVEN`).
  - **Coding worker** — OpenAI Codex (`FIRST REAL CODEX CODING-WORKER BOUNDARY PROVEN`).
  - **Deployment** — Fly Machines (`FIRST REAL EXTERNAL DEPLOYMENT BOUNDARY PROVEN`).
- **Market ingress (Postmark) — NOT complete.** Real emails to an owner-controlled address were
  physically sent (external effect proven), but **no send has yet passed the full independent
  MARKET_ACTION verifier**, so canonical market-action success is **unproven**. The verify-don't-trust
  boundary held every time — no false success was ever recorded.
- **Current blocker:** a Postmark **inbound-domain / Reply-To validation + full-verifier
  observability** defect. **No further live Postmark sends until it is fixed deterministically.**
- **Gate 9** engineering is parked (branch `gate9/reliability-matrix`); **Gate 10** is not started.

> ⚠️ **Do not rerun historical live workflows** (research/Codex/Fly/Postmark) to "confirm" past
> evidence — historical results are authoritative as recorded. See
> `docs/PROGRAMME_STATUS.md` §O.

## Where to start
- **`AGENTS.md`** — entry point and rules for any fresh execution/review agent.
- **`docs/PROGRAMME_STATUS.md`** — authoritative current state, exact blocker, and restart plan.
- **`docs/architecture/ARCHITECTURE.md`** — the frozen four-plane architecture and doctrine.
- **`docs/ADR/`** — accepted Architecture Decision Records.
- **`docs/GATE_0_EXECUTION_RECORD.md`**, **`docs/donor-provenance/`** — historical foundation (Gate 0).

Gate 0 (Preserve & Canonicalise) is complete and preserved as historical background; the first
transient Gate 0 Git object database was not preserved (see ADR-001 and the Gate 0 execution record).
