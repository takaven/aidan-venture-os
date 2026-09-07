# AI-DAN Venture OS — Programme Status & Restart Handoff

> **This is the authoritative CURRENT-STATE document.** It reflects real repository and execution
> state, not historical aspiration. Where a claim is only partially proven, it is labelled as such.
> If any other document disagrees with this one about *current status*, this document is correct and
> the other is stale.

| | |
|---|---|
| **Repository** | `takaven/aidan-venture-os` |
| **Documented main SHA at doc freeze** | `d4b44ff1c6235559d75796d1213c6319c1316424` |
| **Strategic-pivot doc SHA** | `b598aeb8d53d26bc39cb8915f49fadf4c5bc33a3` |
| **Last updated** | 2026-09-07 (strategic pivot) |
| **Current programme phase** | **STRATEGIC PIVOT — Phase-1 judgment evaluation (binding, 2026-09-07).** All infrastructure work is **parked** (Postmark, Fly, Codex, Factory, Operate, Gate 9, Gate 10). The only allowed work is the judgment evaluation harness in `evals/judgment/`. |
| **The existential question** | Does the **same frontier model**, routed through AIDAN's structured decision process (**T**), produce **materially better, cost-adjusted** venture decisions than (1) a strong general prompt (**C0**) and (2) a much simpler distilled-AIDAN prompt (**C1**)? If the structure adds no material lift over C1, the structure is not justified. |
| **Highest-value unresolved uncertainty** | Whether AIDAN's **venture judgment** (opportunity selection, kill decisions, capital allocation quality) is any good — and specifically whether the *structure* earns its complexity. Everything proven so far is *execution/governance plumbing*, not judgment. |
| **Exact recommended first action on resumption** | **Build and run the blinded, same-model judgment evaluation harness in `evals/judgment/`** (design frozen in `evals/judgment/README.md`). Develop the harness on the development cases, keep the holdout sealed, score with objective-dominant rubrics, and apply the precommitted PASS/AMBIGUOUS/FAIL + simplicity-kill decision tree. **Do NOT resume any infrastructure work** (Postmark/Fly/Codex/Gate 9/10) — it is all parked pending this evaluation. |

**Where a fresh agent should start reading:** `README.md` → this document → `evals/judgment/README.md` → `docs/architecture/ARCHITECTURE.md` → relevant ADRs. See also `AGENTS.md` at the repo root.

---

## 0. Strategic pivot (binding, 2026-09-07)

The programme has **paused all infrastructure work** and pivoted to answering its existential
question directly. Rationale: three isolated real boundaries (research, coding worker, deployment)
plus a real-but-unverified market send prove the *plumbing*, but the north-star thesis — that AIDAN's
**structured decision process** produces better venture judgment — remains entirely untested.
Continuing to grind infrastructure edges (the Postmark loop being the cautionary example) defers the
only question that determines whether the whole design is worth building.

**Parked (do not resume this session or without explicit re-authorization):** Postmark market
ingress, Fly deployment, Codex coding worker, Factory/Execution runtime extensions, Operate runtime,
Gate 9 exit, Gate 10. Their documented state below (Sections F–L) remains accurate as *parked*
context; it is **no longer the next action**.

**The only allowed next work** is the Phase-1 judgment evaluation harness under `evals/judgment/`
(see that directory's `README.md` for the frozen experimental design). Its purpose is to measure
whether the same frontier model, routed through AIDAN's staged decision process, beats a strong
general prompt and a simpler distilled prompt on cost-adjusted decision quality — and to **kill the
structure** if a simpler arm captures nearly all of its benefit.

---

## A. North star

AI-DAN Venture OS is a **governed autonomous venture allocator/operator**.

Its north star is **improved long-term portfolio value through evidence-backed capital allocation
and verified execution** — *not* the number of products built or launched.

The fundamental unit is the **highest-value next action**, **not** "build another product".

AIDAN is intended to **discover, evaluate, fund, build, validate, operate, continue, pivot, or kill**
ventures under explicit mandates, evidence, policy and capital constraints. AIDAN itself is the
persistent executive / capital allocator. **AIDAN does not need to be the application-code-writing
agent** — code authoring is a replaceable specialist worker beneath its authority.

---

## B. Four-plane architecture (FROZEN)

The architecture is **frozen** unless execution evidence proves it cannot satisfy a current-gate
requirement (see `docs/architecture/ARCHITECTURE.md`).

1. **Governance** — Venture Mandate, policy, approvals, autonomy, capital controls, permitted state
   transitions.
2. **Intelligence** — AIDAN: opportunity discovery, research synthesis, Kill Case, assumptions,
   experiments, investment decisions, next-best-action allocation. *AIDAN does not write application
   code.*
3. **Execution** — durable Factory runtime, replaceable specialist workers, product/market work, QA,
   deployment, rollback, operations.
4. **Truth** — Evidence Ledger, Capital Ledger, Audit Ledger, canonical venture state, experiments,
   outcomes, costs, machine-verifiable Proof Receipts.

**Canonical consequential path:**

```
ActionRequest → Policy → Approval (if required) → Execution
             → deterministic verification → Proof Receipt → permitted canonical transition
```

Explicitly:
- Worker self-report is **not** authority.
- Generated prose is **not** consequential proof.
- Deterministic provider/system evidence outranks agent claims.
- PostgreSQL is the canonical durable state in the real system.
- Provider-specific integrations are **replaceable adapters** at the edge.
- Venture isolation applies to repository, credentials, data, deployment, permissions, and budget.

---

## C. Evidence model

```
SOURCE → OBSERVATION → CLAIM → INTERPRETATION → ASSUMPTION → DECISION
```

Evidence categories: **OBSERVED**, **SOURCE-CONFIRMED**, **STRATEGIC JUDGEMENT**, **UNVERIFIED**.

A **Proof Receipt** is a machine-verifiable record binding an executed effect to a deterministic
verification. **Consequential SUCCESS cannot exist without one.** This is why, across three real
Postmark sends (Section G), the system never once recorded a false success: each send that could not
be independently verified became canonically `FAILED`, not `SUCCEEDED`.

---

## D. Capital-scarcity doctrine (accepted)

AIDAN must be useful under **severe capital scarcity**. It should preferentially discover the
**cheapest action capable of materially changing a capital decision**.

Realistic capital frames: **$20 / $100 / $300 / $500 / $1,000 / $2,000.** Do **not** frame AI-DAN
around requiring tens of thousands of dollars.

Optimization target: **decision value / uncertainty reduction per dollar.** Weak ventures should die
**cheaply**. (See ADR-037.)

---

## E. Reality / proof maturity

```
DETERMINISTIC → CONTRACT-PROVEN → LIVE-SMOKED → COMPOSED-REAL → QUALIFYING-REAL
```

Complementary proof ladder:
- **A. Contract proof** — the governed shape is correct.
- **B. Deterministic fake-boundary proof** — behaviour proven against an in-memory double.
- **C. Tiny real smoke** — one bounded, authorized live boundary call.
- **D. Composed production proof** — multiple real boundaries composed into one governed loop.

**Isolated live boundary proofs (C) are not the final product.** The programme has several (C)-level
proofs and **no** (D)-level composed real proof yet.

---

## F. What has been proven (accepted real milestones — do NOT rerun)

### Real research boundary — PROVEN
- Run `32594577802`, SHA `d022764fc26aebe864f3a5172094968d705b94aa`.
- Real providers observed: **Claude Sonnet, Tavily, Anthropic**.
- Evidence: 4 receipts, 13 observations, 5 claims, 3 opportunities; terminal `NO_CREDIBLE_OPPORTUNITY`;
  governance delta 0; spend ceiling USD 1; secret-leak protection.
- Milestone: **FIRST REAL RESEARCH BOUNDARY PROVEN.** Do not rerun historical research workflows.

### Real coding-worker boundary — PROVEN
- PR #11, merge `9047b20a2fdf26f70e92002e039d6c9eaf2d3898`; real Codex smoke `33480801402`.
- Observed: exactly one real Codex process invocation; VERIFIED; committed USD 0.0095; provider
  contact observed.
- Milestone: **FIRST REAL CODEX CODING-WORKER BOUNDARY PROVEN.** Do not rerun.

### Real deployment boundary — PROVEN
- Fly live smoke `33616717418`, SHA `b416825c16667967ea4c2bfe9dd8aefd341e2ceb`.
- Observed: one Fly Machine created; provider effect observed; machine started; health 200; expected
  marker observed; `DEPLOYMENT_RELEASE` Proof Receipt VERIFIED; final SUCCEEDED; lifecycle correctly
  bounded; cleanup confirmed; governance delta 0; conservative committed ceiling USD 0.05; secret-leak
  PASS.
- Milestone: **FIRST REAL EXTERNAL DEPLOYMENT BOUNDARY PROVEN.** Do not rerun absent a newly
  justified boundary. (Fly is the *replaceable* first deployment adapter — ADR-036.)

---

## G. Market ingress — current state (precise)

Postmark is the **current unresolved Gate-8 real boundary.** Accepted server **`20453649`**;
owner-controlled sender/recipient used for the smoke **`admin@takaven.com`** (owner-declared).

### G.1 Initial auth / recovery history
- Owner-ingress run `33974829245` → historical `RECOVERY_REQUIRED` (external effect **ambiguous**).
- Read-only recovery `34012931530` → `GET /server` returned **401**. The historical send remains
  ambiguous. **Never rerun that historical action.**

### G.2 Credential repaired
- Zero-send live preflight `34025525151` → **PASS**: exact server identity `20453649`, Live server,
  provider contact OBSERVED, zero sends, secret-leak PASS.

### G.3 First confirmed real owner send
- Run `34025866162`; MessageID `eb8e2cff-b359-481c-8e50-81482c0e7a15`.
- Observed: exactly one `POST /email`; provider effect OBSERVED; Postmark returned the MessageID;
  **verifier REJECTED**; canonical action **FAILED**; no false Proof Receipt; lifecycle not
  over-promoted; governance delta 0.
- Read-only field diagnostic `34030402681`. **Among the fields it checked**: content ✓, recipient ✓,
  subject ✓, sender raw-equality ✗, sender parsed-mailbox-equality ✓, stream ✓, sandbox ✓,
  action-correlation ✓, MessageID ✓.
  - **Do NOT state that sender was proven the only verifier failure.** It was only the sole mismatch
    among the diagnostic's **covered** fields. The diagnostic did **not** cover every authoritative
    verifier field (notably **Reply-To**).

### G.4 Sender-representation repair
- PR #22, merge `d4b44ff1c6235559d75796d1213c6319c1316424`.
- Repair: sender identity now compares **strict parsed mailbox** identity, not raw `From` header
  representation (the same `_normalize_email` normalizer already used for recipient and Reply-To).
- Read-only recovery after repair `34034512480` → `RECOVERY_CONFIRMED_SENT`. This proved the existing
  provider message matched the **recoverable subset**.
  - **Do NOT claim this was equivalent to full `PostmarkActionVerifier` success.** The recovery
    deliberately cannot reconstruct all original authoritative fields after ephemeral-DB destruction
    (it excludes Reply-To and run-specific canonical IDs).

### G.5 Final canonical attempt under sender repair
- Run `34038461081`; action request `3cebb6d6-dbff-48ff-9944-9d38f22f3902`; MessageID
  `7342c922-4e6d-498a-abaa-dff7e5448edc`.
- Observed: exactly one `POST /email`; provider contact OBSERVED; send effect OBSERVED; **full
  verifier REJECTED**; MARKET_ACTION Proof Receipt `9b794ec7-…` **FAILED** (evidence_hash
  `c05594d4…`); final canonical status **FAILED**; lifecycle unchanged; governance delta 0;
  secret-leak PASS; failure_phase `POST_SEND_VERIFY`.

**Therefore:** real market-ingress **EXTERNAL EFFECT has been proven** (real emails reached the
owner). **CANONICAL MARKET_ACTION SUCCESS has NOT been proven.** **Do not claim Gate 8 market ingress
is complete.**

---

## H. Current Postmark defect — exact restart point

Identified by repository inspection (no provider call):

- `.github/workflows/gate8-postmark-owner-ingress-smoke.yml` injects
  `POSTMARK_INBOUND_DOMAIN: ${{ vars.POSTMARK_INBOUND_DOMAIN }}`.
- `packages/core/aidan_core/market/postmark_live_smoke._source()` accepts an **empty** inbound domain.
- `postmark_live_smoke.main()` does **not** currently require `POSTMARK_INBOUND_DOMAIN` before
  execution.
- `postmark._postmark_contract()` unconditionally constructs a Reply-To of the form
  `reply+<hash>@<source.inbound_domain>`.
- The full verifier checks **`REPLY_TO_IDENTITY`** (`_normalize_email(msg.ReplyTo) ==
  _normalize_email(frozen reply_to)`).
- The earlier field diagnostic **omitted** Reply-To. The read-only recovery **also excludes** Reply-To
  because the expected Reply-To depends on run-specific canonical IDs destroyed with the ephemeral
  smoke database.

**Deterministic repair requirement when work resumes:**
1. Require a **valid** inbound domain before ANY provider call.
2. Validate generated Reply-To **syntax** before provider contact.
3. Do **not** silently drop or substitute Reply-To.
4. Retain strict `REPLY_TO_IDENTITY`.
5. **Persist/surface ALL full-verifier PASS/FAIL checks** in sanitized live evidence (close the
   observability gap that let the failing check disappear with the ephemeral DB).
6. Make diagnostics **explicit** about any authoritative fields they do not check.

**Status of the Reply-To explanation:** the exact historical failing check was **not preserved** (it
lived only in the destroyed ephemeral DB). Therefore Reply-To is:
- a **confirmed configuration/observability defect**, and
- the **strongest remaining failure explanation** (by elimination — every other verifier check is
  either self-consistent within a fresh run, proven by the preflight, or confirmed matching by the
  field diagnostic), but
- **not retrospectively proven** as the exact historical failed check.

> **NO MORE LIVE POSTMARK TRIALS SHOULD OCCUR UNTIL THIS IS FIXED DETERMINISTICALLY.**

---

## I. Why the Postmark loop happened (do not repeat)

- The live smoke allowed **verifier per-check detail to disappear** with the ephemeral DB.
- Diagnostics covered only a **subset** of full verifier authority.
- **Partial** recovery evidence (`RECOVERY_CONFIRMED_SENT`) was mistakenly treated as **full-verifier
  equivalence**.
- Further live sends were authorized **before complete verifier observability existed.**

**New rule — after a consequential live failure, do not buy information one live action at a time.**
Before another consequential attempt:
1. Preserve **complete** terminal verifier evidence.
2. Validate **every** provider-dependent configuration field.
3. Reproduce the failure **deterministically** (fake/contract tests).
4. Close the defect in fake/contract tests.
5. Then perform **at most one** newly justified live boundary.

---

## J. Gate / programme status

| Gate | Status |
|---|---|
| **Gate 0** | COMPLETE / historical foundation preserved (`docs/GATE_0_EXECUTION_RECORD.md`). |
| **Gates 1–7** | Engineering foundations implemented sufficiently to support current Gate-8 work. (Do not rewrite historical detail beyond repo evidence.) |
| **Gate 8** | **PARKED (was active).** Proven individual real boundaries: research, coding worker, deployment. Unresolved and **parked**: canonical real **market-ingress** verification (Sections G–I). Not the next action — see Section 0. |
| **Phase-1 judgment evaluation** | **ACTIVE / ONLY ALLOWED WORK.** Blinded same-model judgment harness in `evals/judgment/` (skeleton + frozen design present; implementation pending). This is the current programme focus. |
| **Gate 9** | Engineering work parked on branch `gate9/reliability-matrix`, SHA `2aa69844f971afad6a25df5235f552348dab3715`; historical CI `32557590457` (1054 tests passed). Status: **GATE 9 ENGINEERING EVIDENCE COMPLETE — FORMAL EXIT DEFERRED PENDING GATE 8 SEQUENCE.** **Do NOT merge Gate 9 merely because documentation is being updated.** |
| **Gate 10** | NOT STARTED / no authorization. |

---

## K. The real objective — venture judgment

**The programme must NOT return to endless isolated infrastructure completion.**

> **Pivot note (2026-09-07):** the judgment question is now being tested **directly and offline
> first**, via the blinded same-model evaluation harness in `evals/judgment/` (Section 0), *before*
> any further composed real-world loop. The thin end-to-end venture loop below remains the eventual
> objective, but it is now **downstream** of the harness proving that AIDAN's structure adds material,
> cost-adjusted judgment lift. If the harness shows a simpler arm captures nearly all the benefit, the
> structure is simplified before any composed-loop investment.

The eventual **thin genuine end-to-end venture loop:**

```
research → opportunity judgment → Kill Case → assumptions → small capital decision
        → build (if justified) → deploy → market action → observed outcome
        → continue / pivot / kill decision
```

The purpose is to test **AIDAN's JUDGMENT, not merely its plumbing.** AIDAN must eventually
distinguish, under contradictory/incomplete evidence:
1. a genuinely attractive opportunity;
2. a seductive but weak opportunity;
3. an obviously weak opportunity.

Evaluation must judge **decision quality against evidence/outcomes**, not document completeness.

---

## L. Known post-Gate-8 / Alpha blockers (pending, not necessarily next)

- **Raw webhook/reply payload persistence creates a PII/privacy issue** — must be fixed before
  prospect outreach / real inbound processing.
- **Autonomy classifier contradiction** — `packages/core/aidan_core/alpha/autonomy.py` has a
  simulated-vs-real-provider inconsistency.
- Repeated `NO_RESPONSE` commercial outcomes may not structurally resolve.
- Research dropped-link telemetry / referential validation should be hardened.
- Proposal-validation invariant completeness needs review.
- Portfolio-allocator north-star / judgment gap remains.
- **Research-provider spend governance gap** — Tavily/Anthropic execution needs canonical
  ActionRequest capital governance.
- **True composed real end-to-end Alpha has not yet been proven.**
- **Profitable autonomous venture creation remains completely unproven.**

---

## M. Grok / operate-runtime design rule (accepted — ADR-038)

**Do NOT build a large generic browser / computer-use / SaaS integration substrate by default.**

When persistent browser/computer/SaaS operation first becomes necessary, run a **bounded Grok Bot
substitution spike.** Grok is a candidate **replaceable execution substrate** *inside* AIDAN
governance. Grok does **not** replace: Venture Mandate, evidence model, Kill Cases, capital
allocation, policy, approvals, Proof Receipts, lifecycle authority, or continue/pivot/kill decisions.

Preferred sequence: market ingress → thin real venture loop → the need for persistent computer/SaaS
operation becomes concrete → bounded Grok substitution spike → use Grok if adequate → otherwise build
only the missing capability.

---

## N. Current confidence / claim boundary

- **High confidence:** governance kernel; consequential controls; Proof Receipt architecture;
  deterministic verification; real research provider boundary; real coding-worker boundary; real
  deployment boundary.
- **Medium / partial:** real market-ingress physical effect (proven); cross-boundary composition
  (unproven).
- **Low / unproven:** opportunity-selection quality; product judgment; market judgment; autonomous
  capital-allocation quality; profitable venture creation.

**Current correct one-line description:**
*A strongly evidenced autonomous-venture execution/governance substrate with the core
venture-judgment thesis still unproven.*

---

## O. Historical real runs — DO NOT RERUN

Historical failure/success evidence remains historical. **Never rerun a workflow merely because a
fresh agent wants to "confirm" it.**

- **Research:** `32579135040`, `32583150601`, `32587737019`, `32589470701`, `32590352523`,
  `32591612780`, `32594577802`.
- **Codex:** `33264176338`, `33473830970`, `33480801402`.
- **Fly:** `33604843564`, `33608688301`, `33616717418`.
- **Postmark:** `33974829245`, `34012931530`, `34025525151`, `34025866162`, `34028744199`,
  `34030402681`, `34034512480`, `34038461081`.

---

## P. Exact resumption plan

When work resumes (strategic pivot in effect — Section 0):
1. Inspect current live `main`.
2. Read `README.md`.
3. Read this document (`docs/PROGRAMME_STATUS.md`), especially **Section 0**.
4. Read **`evals/judgment/README.md`** — the frozen experimental design for the judgment harness.
5. Read `docs/architecture/ARCHITECTURE.md`; consult ADRs only as needed.
6. Verify no material repo drift from the pivot (main should be
   `b598aeb8d53d26bc39cb8915f49fadf4c5bc33a3` plus the pivot PR, unless later work landed).
7. **Build the Phase-1 judgment evaluation harness in `evals/judgment/`:** author the development
   cases, the three arm prompts (C0 general, C1 distilled-AIDAN, T AIDAN-staged), the runners, and the
   objective-dominant scorer — developing only against the **development** cases while the **holdout**
   stays sealed.
8. **No live research or provider calls** — all arms run the **same model** on **fixed frozen evidence
   packs**. Track cost per arm.
9. Run all three arms blinded, score with the frozen rubric, and apply the precommitted
   **PASS / AMBIGUOUS / FAIL** decision tree plus the **simplicity kill test** (if C1 captures nearly
   all of T's benefit → simplify the structure).
10. Report the result and hand back to the orchestrator. **Do NOT** resume any parked infrastructure
    (Postmark/Fly/Codex/Gate 9/10) unless the evaluation outcome plus explicit authorization direct it.

**All infrastructure work is parked. No prospect/customer outreach is authorized. Gate 9 remains
parked. Gate 10 is not started.**
