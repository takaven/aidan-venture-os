# AGENTS.md — Entry point for any fresh execution/review agent

You are working on **AI-DAN Venture OS**, a governed autonomous venture allocator/operator. Before
doing anything, orient yourself.

## Read first, in this order
1. `README.md` — 2-minute orientation.
2. `docs/PROGRAMME_STATUS.md` — **authoritative current state and restart plan.** Start with its
   **Section 0 (strategic pivot)**. This is the most important document.
3. `evals/judgment/README.md` — the frozen experimental design for the **only currently allowed
   work** (the judgment evaluation harness).
4. `docs/architecture/ARCHITECTURE.md` — the frozen architecture and doctrine.
5. Relevant ADRs under `docs/ADR/` — only as needed.

## Source precedence (highest wins)
1. Latest owner mandate.
2. Current canonical programme status (`docs/PROGRAMME_STATUS.md`).
3. Frozen architecture (`docs/architecture/ARCHITECTURE.md`).
4. Accepted ADRs.
5. Current code + deterministic tests.
6. Historical execution records.

## Core execution rules
- **Do not reopen frozen architecture** without proof of necessity.
- **Do not rerun historical live workflows** (see `docs/PROGRAMME_STATUS.md` §O) — historical
  evidence stays historical.
- **Do not infer success from worker self-report** — deterministic verification and Proof Receipts
  are the only consequential authority.
- **Do not change canonical lifecycle** without verified proof.
- **Do not expand scope autonomously.**
- **Consequential provider actions** (send email, deploy, spend, run a coding worker, call a research
  provider) require **explicit bounded authorization** — one action, one recipient/target, a spend
  ceiling, no retry.
- **Diagnose live failures deterministically** (fake/contract tests + complete evidence) **before any
  new consequence.** Do not buy information one live action at a time.

## Current focus — strategic pivot (binding, 2026-09-07)
- **All infrastructure work is PARKED:** Postmark, Fly, Codex, Factory/Execution, Operate, Gate 9,
  Gate 10. Do not resume any of it without explicit re-authorization.
- **The only allowed work is the Phase-1 judgment evaluation harness** in `evals/judgment/`. The
  existential question: does the **same frontier model**, routed through AIDAN's structured decision
  process (**T**), produce **materially better, cost-adjusted** venture decisions than a strong
  general prompt (**C0**) and a much simpler distilled-AIDAN prompt (**C1**)? If the structure adds no
  material lift over C1, **the structure is not justified** and must be simplified.
- **First action on resumption:** build and run that blinded, same-model harness per the frozen design
  in `evals/judgment/README.md` (same model for all arms; fixed frozen evidence packs; no live
  research; develop on the development cases; keep the holdout sealed; objective-dominant rubric;
  precommitted PASS/AMBIGUOUS/FAIL + simplicity-kill decision tree; cost tracking required).
- The **parked** Postmark market-ingress blocker (`docs/PROGRAMME_STATUS.md` §H) is documented context
  only — **not** the next action.
- **No prospect/customer outreach is authorized.** No live provider calls for the harness.

## What "done" looks like here
Success is **improved long-term portfolio value through evidence-backed capital allocation and
verified execution** — not the number of products built. The unproven core is AIDAN's **judgment**
(opportunity selection, kill decisions, capital allocation), not its plumbing.
