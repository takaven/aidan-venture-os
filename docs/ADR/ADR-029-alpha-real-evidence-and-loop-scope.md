# ADR-029 — Alpha Real-Evidence Provenance & Loop Scope

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 4)

## Context

Two evidentiary seams remained before a real Alpha run: (1) a `MARKET_ACTION` proof obtained
through a live provider was not canonically distinguishable from one obtained through the
`FakePostmarkTransport` fixture; and (2) autonomy assistance was classified per-venture, so an
unrelated intervention before or after a run could contaminate its classification. Slice 4 closes
both with the smallest durable additions and then freezes production for the real run.

## Decisions

### Durable REAL vs SIMULATED evidence origin

`external_evidence_origin` (migration 0024) binds the exact VERIFIED `MARKET_ACTION`
`proof_receipt` (and its attempt) to a trusted `origin_kind` — `REAL_PROVIDER` or `SIMULATED`.
It is written by trusted execution code (`market/origin.py`, called from `verify_postmark_action`
only on VERIFIED) using the transport's OWN declared origin: `PostmarkHttpTransport.origin_kind =
REAL_PROVIDER`, `FakePostmarkTransport.origin_kind = SIMULATED`. There is no caller/worker path to
set it — no `is_real=True`, no `evidence_class` parameter, no metadata/source-ref/MessageID
inference. `REAL_PROVIDER` means only "provider-backed evidence path"; it asserts nothing about
commercial success. `action_reality` derives `REAL` iff a `REAL_PROVIDER` origin exists for the
action's verified proof, else `SIMULATED` (the Gate-7 local channel and every fixture are
SIMULATED). A `NO_RESPONSE` completion inherits the reality of its anchor action proof, so
synthetic elapsed time never becomes real market evidence.

### Loop boundaries derived from lineage — no run table

A closed loop is bounded by canonical lineage: the starting recommendation → its
`investment_decision_record` (`source_recommendation_id`, `resulting_action_id`) → the market
action + VERIFIED proof → its observation / no-response completion → the next recommendation. No
`closed_loop_run`/workflow table is introduced. `classify_loop(start_recommendation_id,
next_recommendation_id)` reconstructs the interval from recommendation/decision `created_at`
timestamps.

### Loop-scoped autonomy

Assistance is classified over the exact `[start_at, end_at)` interval (the next recommendation, or
for a terminal decision the decision instant). An `alpha_intervention` before the loop, after it,
or on another venture is ignored; one inside the interval yields `HUMAN_ASSISTED`. A predefined
`approval` inside the loop remains compatible with `CLEAN` and is never recorded as an
intervention. `alpha_intervention.related_action_request_id` is composite-FK-bound to the
venture, so an intervention cannot reference another venture's action.

### Three independent dimensions; synthetic loops are never eligible

`classify_loop` returns `completeness` (COMPLETE/INCOMPLETE), `assistance_class` (CLEAN/
HUMAN_ASSISTED), and `reality_class` (REAL/SIMULATED) as separate axes.
`eligible_clean_real_alpha` requires all of COMPLETE + CLEAN + REAL, so every Slice-4 fixture
(SIMULATED) is ineligible — no synthetic run can be reported as a real Alpha success. A complete
loop may have a negative outcome (BOUNCED/UNSUBSCRIBE/negative reply/NO_RESPONSE) or conclude in
`KILL`; commercial negativity does not invalidate the architecture, and a loop with no next
canonical allocation is `INCOMPLETE` rather than a false success.

### No new authority

Origin recording and loop classification are truth projections: they create no investment
decision, ActionRequest, policy decision, lifecycle transition, or capital entry, and mutate no
proof/observation/completion/recommendation. The allocator remains the sole author of decisions.

## Consequences

- Migration `0024` adds `external_evidence_origin` (append-only); migrations `0001–0023`
  unchanged. New modules `aidan_core/market/origin.py` and `aidan_core/alpha/loop.py`;
  `verify_postmark_action` binds the origin on VERIFIED. No `closed_loop_run`, no capability, no
  dependency, no network. The Postmark authenticity boundary is unchanged. Real Alpha execution
  (live token, sender/recipient authorization, network, webhook) remains a later owner-approved
  Slice 5 — operationally gated, not a code change.
