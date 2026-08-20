# ADR-031 — Gate 8 Outcome & Next-Decision Binding

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 4, ZIP-audit correction)

## Context

A full-repository audit of the prior freeze candidate found that several load-bearing invariants
were weaker than Gate 8 requires: the loop completed at a recommendation rather than a committed
decision; provider events were attributed by Metadata rather than the exact proven MessageID; a
reply's sender was not checked against the authorized recipient; the attestation's guarantee was
overstated; and an error path referenced an unimported name. This correction binds each to the
exact canonical fact. The prior freeze is retired.

## Decisions

### A closed loop completes at the committed next decision, not a recommendation

For a non-terminal market loop, `classify_loop` marks it `COMPLETE` only when the validated next
recommendation is committed into a canonical `investment_decision_record` (the actual next
allocation) — a recommendation alone is insufficient. The loop interval therefore ends at that
decision's timestamp, so any unplanned human intervention between the next recommendation and its
commitment falls inside the loop and yields `HUMAN_ASSISTED`. A terminal next decision (KILL/HOLD)
still completes the loop without requiring a further ActionRequest; venture survival and positive
evidence are not required.

### Provider events bind to the exact proven MessageID

Delivery/Bounce ingestion resolves the action by requiring the event's MessageID to equal the
exact proven outbound MessageID (`execution_result.external_result_id`) of that action's VERIFIED
`MARKET_ACTION` proof. Provider Metadata (`market_action_spec`) is only a secondary cross-check,
never the primary key. A different provider message carrying copied canonical Metadata is
rejected.

### A reply must come from the authorized recipient

Inbound reply ingestion, after Basic-Auth and MailboxHash correlation, requires the normalized
inbound `From` to equal the exact authorized outbound recipient (via the same `RecipientResolver`
used to send). MailboxHash proves which action/address was contacted; it does not prove who
replied. A reply from any other sender to the unique reply address is rejected, so a `REPLIED`
`REAL_PROVIDER` observation is attributable buyer evidence. Address matching is deterministic
(exact address extraction + case-fold); no fuzzy matching.

### Accurate attestation trust-model claim

REAL provenance is still writable only through the trusted kernel/provider paths: `origin_kind`
is never a caller argument, a worker result cannot confer REAL, a transport subclass is not
trusted (exact-type gate), and the `_PostmarkVerifiedProviderState` requires the trusted
provider-path key. The claim is corrected to its true scope: this is a kernel/provider-path
boundary under the current in-process trust model (specialist workers hold no DB authority) — it
is NOT a language-level guarantee against arbitrary in-process malicious code (an in-process
sandbox is a deliberate later-gate concern, not solved here).

### Canonical error path

`record_observation_origin` raises the canonical `NotFoundError` for a nonexistent observation
(the name is now imported), not a `NameError`.

## Consequences

- No migration (0001–0025 unchanged), no capability/dependency change, no provider account/network.
  Production changes are confined to `alpha/loop.py`, `market/postmark.py`, and `market/origin.py`.
- The previous production freeze (`a0676b0`) and its held-out suite are retired; the held-out
  suite is removed and will be re-authored against the new freeze after a fresh audit. Development
  helpers now commit the next recommendation before asserting a COMPLETE loop.
