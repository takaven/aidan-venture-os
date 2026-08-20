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

### The worker avoids blind duplicate dispatch against provider state

`PostmarkEmailWorker` no longer issues a bare `send_email`. `_send_reconcilably`:

1. **Never duplicates a reconcilable send.** Before any POST it searches provider state by the frozen
   correlation (`find_outbound_by_correlation`) and, if exactly one *fully-compliant* correlated
   message already exists (`_message_matches_frozen`: correlation + content + recipient + Subject +
   Reply-To + sender + MessageStream, not Sandboxed), it captures that MessageID instead of sending.
2. **Reconciles ambiguity.** If the POST fails after dispatch, it searches again: exactly one
   compliant message → capture it (the send did occur, no re-POST).
3. **More than one correlated message → fail closed** (ambiguous duplicate; never resolve to an
   arbitrary id).

Known **pre-send** failures (wrong frozen server, wrong credential/server, Sandbox server, wrong
recipient/sender/source, and a deterministic 4xx `PostmarkSendRejected`) carry no external effect and
remain ordinarily retryable — they never reach the ambiguous path.

### Exactly-once is not provable from an empty eventually-consistent search — fail closed

An empty `find_outbound_by_correlation` result is an *observation* ("no matching message returned
now"), never proof the POST did not occur: provider search is eventually consistent and can lag or be
briefly unavailable. Conflating "zero now" with "proven absence" would let a later attempt blind-POST
a possibly-sent email. Therefore, when the consequential boundary has been crossed ambiguously (a
network/5xx fault, or a pre-existing multiple-match) and reconciliation cannot resolve it to exactly
one compliant message, the worker raises the generic `AmbiguousExternalEffectError`. The factory maps
it to a durable **`RECOVERY_REQUIRED`** state that is **not auto-claimable** (`execute_action` refuses
it), so no ordinary later attempt — and no `max_attempts > 1` — can re-issue the effect. The budget
reservation is held. Only **explicit** recovery (`reconcile_postmark_recovery`) resolves it: exactly
one compliant provider message → capture it and complete through the normal deterministic verifier
(one proof); zero (still not proven absent) or multiple (duplicate) → stay `RECOVERY_REQUIRED`, never
redispatch and never select arbitrarily. Candidate selection (`_message_matches_frozen`) is not the
authority — the canonical `PostmarkActionVerifier` still runs, and a **rejected** recovery
verification (e.g. wrong actual Server ID or a non-Live/Sandbox server) is not evidence the original
effect did not occur: `verify_and_complete` keeps such an action `RECOVERY_REQUIRED` (never the
ordinary retryable `VERIFICATION_FAILED`→`PENDING` path), so a failed reconciliation can never reopen
automatic dispatch. This is **no blind duplicate dispatch**, not an absolute distributed-systems
exactly-once claim.

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
