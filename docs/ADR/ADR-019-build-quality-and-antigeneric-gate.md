# ADR-019 — Build Quality & the AntiGeneric Gate

**Status:** Accepted
**Gate:** 5 — Venture Substrate + Builder Quality (Slice 3)

## Context

Slice 2 established deterministic Technical quality for a built candidate. A
technically valid candidate is not sufficient: the programme must independently
decide whether a candidate implements the intended venture — its buyer, problem,
workflow, differentiators, and approved commercial thesis — and whether it is
genuinely venture-specific rather than a generic app reskinned. Slice 3 adds the
four remaining quality dimensions and the overall decision, without collapsing
quality into a subjective review or a single score.

## Decisions

### Five distinct dimensions; no scalar score; no compensation

Quality is decided across five DISTINCT dimensions — Technical (reused from Slice 2,
never re-implemented), Product, Experience, Commercial, AntiGeneric. There is no
scalar or weighted score and no compensation: a high Technical result cannot offset
a Product FAIL. Overall quality is derived in the kernel as PASS iff all five
dimensions PASS; it is never stored as an independent, caller-supplied truth.

### Evidence is separate from the decision, and kernel-derived

`build_quality_evidence` (append-only) records per-(manifest, dimension, criterion)
observations; `build_quality_assessment` (append-only) records the canonical
per-dimension PASS/FAIL the kernel derives from that evidence. A dimension is PASS
iff all its required criteria PASS. Evidence carries an explicit `source_type`
(`KERNEL_DERIVED`, `TEST_RESULT`, `STRUCTURAL_MANIFEST`, `REVIEW_OBSERVATION`) so a
worker-declared structural fact is not treated as strongly as a kernel-derived one.

### Worker and reviewer self-reports are inert

A builder's `product_pass`/`overall_pass`-style claims change no verdict. A model
reviewer may only contribute `REVIEW_OBSERVATION` evidence (observation +
interpretation + source refs); any verdict it asserts is ignored. No live model or
provider is added; the reviewer interface is deterministic and future-model-ready.
The candidate's declared product structure (workflows, features, vocabulary, CTA,
differentiators implemented) is worker-declared `STRUCTURAL_MANIFEST` evidence,
judged deterministically against the frozen build_spec; deeper code-derivation of
that structure is deferred.

### Product / Experience / Commercial evaluate against the frozen intent

- **Product**: primary workflow implemented; required capabilities present;
  excluded capabilities absent; differentiators implemented.
- **Experience**: primary journey complete and dead-end-free; domain language
  present; declared required states covered — not visual taste.
- **Commercial**: the candidate targets the approved buyer, exposes a conversion
  path where the spec requires one, and does not contradict the approved offer. This
  is implementation alignment to the already-approved Gate 2/3 thesis — it asserts
  nothing about real demand, willingness-to-pay, acquisition, or revenue, and adds
  no market-fact store.

### AntiGeneric is judged relative to the build_spec, not by keyword ban

The load-bearing question — "could this product plausibly belong to another business
if only the logo/headline changed?" — is answered from observable candidate evidence
relative to the frozen spec: the venture-specific primary workflow must be
implemented, the required differentiators materially present, and the candidate must
carry venture substance rather than being a generic substitute (a stock
dashboard/CRUD/chat shell in place of the required workflow). Generic patterns count
against a candidate only when they SUBSTITUTE for venture-specific functionality; a
venture that genuinely requires a dashboard or a conversational workflow is not
penalised for it.

### No canonical state change in Slice 3

Recording quality evidence and per-dimension verdicts is evidence only: Slice 3
creates no Proof Receipt (the Gate 4 worker-execution proof is not repurposed as a
product-quality proof), performs no lifecycle transition (`BUILDING → OPERATING` is
not triggered here), and performs no merge or deployment. Wiring an overall-quality
proof to a consequential transition is deferred to the Gate 5 exit composition.

### History is append-only

Evidence and assessments are immutable; a corrected build produces a new attempt →
manifest → assessment, and the earlier FAIL history is preserved. Changed product
intent still requires a new governed build_spec/action (Slice 1).

## Consequences

- Migration `0017` adds `build_quality_evidence` and `build_quality_assessment`;
  migrations `0001–0016` unchanged. No deployment/customer/revenue tables, no score.
- New production module `aidan_core/build/quality.py` (small explicit quality kernel)
  and the `assess_build_quality` composition; the Builder remains an ordinary Gate 4
  `WorkerAdapter`.
- Dependencies: none (stdlib only). No AI SDK, no browser automation.
