# AGENTS.md — Entry point for any fresh execution/review agent

You are working on **AI-DAN Venture OS**, a governed autonomous venture allocator/operator. Before
doing anything, orient yourself.

## Read first, in this order
1. `README.md` — 2-minute orientation.
2. `docs/PROGRAMME_STATUS.md` — **authoritative current state, restart plan, and the exact current
   blocker.** This is the most important document.
3. `docs/architecture/ARCHITECTURE.md` — the frozen architecture and doctrine.
4. Relevant ADRs under `docs/ADR/` — only as needed.

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

## Current pause point
- The programme is **paused at Gate 8 market ingress.** The current blocker is the Postmark
  **Reply-To / inbound-domain configuration + full-verifier observability** defect
  (`docs/PROGRAMME_STATUS.md` §H). **No more exploratory live sends until it is fixed
  deterministically.**
- **No prospect/customer outreach is authorized.**
- **Gate 9** engineering is **parked** on `gate9/reliability-matrix` (do not merge it as a side
  effect).
- **Gate 10** is **not started.**

## What "done" looks like here
Success is **improved long-term portfolio value through evidence-backed capital allocation and
verified execution** — not the number of products built. The unproven core is AIDAN's **judgment**
(opportunity selection, kill decisions, capital allocation), not its plumbing.
