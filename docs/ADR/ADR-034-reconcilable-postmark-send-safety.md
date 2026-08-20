# ADR-034 — Reconcilable Postmark Send Safety

**Status:** Accepted
**Gate:** 8 — One Real Closed Loop (Slice 4, ZIP-audit correction)

## Context

`market/postmark.py` declared `POSTMARK_SEND_SAFETY = "RECONCILABLE"` and its comments said an
ambiguous send must be reconciled before retry, but the constant was wired nowhere:
`execute_postmark_action` called `factory_runtime.execute_action` without a `safety_mode`, so the
consequential email send silently inherited the factory default `IDEMPOTENT`, and there was no
production reconciliation step. A real send could therefore behave unsafely:

```
POST /email reaches Postmark -> Postmark accepts + sends
        -> the network response is lost / times out
        -> the worker raises, the factory records WORKER_ERROR
        -> no execution_result captures the MessageID
        -> with attempts remaining, a fresh attempt may POST the email again (duplicate)
```

Email dispatch is the consequential real-world action for the Alpha; a provider side effect may
occur while canonical state believes execution failed. The audit's misleading
`test_40_ambiguous_result_reconciles_against_provider_state` began from a *successful* captured send
and only re-verified — it never exercised "provider accepted, client never received the MessageID".

## Decisions

### The worker guarantees exactly-once against provider state

`PostmarkEmailWorker` no longer issues a bare `send_email`. `_send_reconcilably`:

1. **Never duplicates.** Before any POST it searches provider state by the frozen correlation
   (`find_outbound_by_correlation`) and, if exactly one *fully-compliant* correlated message already
   exists (`_message_matches_frozen`: correlation + content + recipient + Subject + Reply-To + sender
   + MessageStream, not Sandboxed), it captures that MessageID instead of sending. So a retry after a
   possibly-sent attempt reconciles rather than re-POSTs.
2. **Reconciles ambiguity.** If the POST raises (network fault after dispatch) or returns no usable
   MessageID, it searches again: exactly one compliant message → capture it (the send did occur, no
   re-POST); **zero or unreconcilable → fail closed** (`MarketAuthorityError`), never a blind retry
   and never a fabricated id.
3. **More than one correlated message → fail closed** (evidence of a prior duplicate; never resolve
   to an arbitrary id).

Known **pre-send** failures (wrong frozen server, wrong credential/server, Sandbox server, wrong
recipient/sender/source) are rejected *before* any POST and never reach the reconciler — they carry
no external effect and remain ordinarily retryable.

### The dispatch path declares the real safety mode

`execute_postmark_action` passes `safety_mode = POSTMARK_SEND_SAFETY` (`RECONCILABLE`) to
`execute_action`; a real send no longer inherits `IDEMPOTENT`. Ordinary retry semantics for
proven-no-effect failures are preserved (`max_attempts` is unchanged); the exactly-once guarantee is
a **code invariant in the worker**, not a reliance on operational `max_attempts = 1`.

### Reconciliation reuses the frozen correlation; no new idempotency token

The reconciliation key is the already-frozen Postmark Metadata (`venture`, `market_action_spec`,
`action_request`, `action_spec_hash`). The real transport searches Postmark's outbound messages by
`metadata_*` filters and loads each candidate's full details for exact-identity checking. No new
token, table, outbox, queue, provider-account store, or migration.

## Consequences

- No migration (0001–0025 unchanged), no dependency/capability change. Production change confined to
  `market/postmark.py`. Fakes/stubs model the outbound correlation search (no network). Adversarial
  tests cover: lost-response reconcile (one POST, one proof), unresolved ambiguity (fail closed, no
  proof/REAL origin, no second POST), multiple correlated matches (fail closed), a genuine
  no-usable-MessageID rejection (no false success), and a `max_attempts > 1` retry that reconciles a
  prior ambiguous send instead of blind-dispatching. All prior server/source/recipient/Subject/
  Reply-To/result-id/Live invariants are preserved.
