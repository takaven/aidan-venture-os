# ADR-022 — Proof-Gated Deployment Lifecycle

**Status:** Accepted
**Gate:** 6 — Verification & Deployment (Slice 3)

## Context

Slice 2 produced a deterministically VERIFIED deployment `proof_receipt`
(`verification_type=DEPLOYMENT_RELEASE`) but performed no lifecycle change. Slice 3
projects that proof into the smallest permitted canonical outcome and exercises
failure/recovery, keeping build quality, deployment run status, investment decision,
and lifecycle state distinct.

## Decisions

### No `deployment_record` — canonical state already answers deployment truth

The existing immutable chain fully reconstructs a successful deployment:
`release_candidate → deploy ActionRequest → execution_attempt →
deployment-specific VERIFIED proof_receipt → deployment_target`. `verified_deployment`
and `latest_verified_deployment` derive "which release is deployed" and "the latest
verified deployment per venture/target" from that chain. No mutable `current_release`
or `deployment_record` is stored, and no migration is added (migrations remain
`0001–0018`).

### Only a deployment-specific VERIFIED proof authorizes OPERATING

`promote_verified_deployment` is the ONE trusted composition that transitions
`BUILDING → OPERATING`. It requires a VERIFIED proof whose `verification_type` is
`DEPLOYMENT_RELEASE` bound to this exact venture/action/attempt/release; a generic
Gate-4 worker-execution proof (e.g. `STRUCTURED_CONTRACT`) is insufficient. The
transition happens ONLY through the sole lifecycle authority
(`lifecycle.transition_cur`) — deploy code never writes `lifecycle_state` directly.
Worker `deployed=true`/`lifecycle=OPERATING` claims and verifier claims outside the
canonical proof are inert.

### Governance rechecked at promotion; state machine enforced

Promotion reloads canonical state, rechecks the kill switch at promotion time (a kill
engaged after the proof blocks OPERATING), reconfirms the build is still Gate-5
quality-qualified, and relies on the permitted-transition set — so a venture not in
`BUILDING` cannot skip the state machine. Promotion is idempotent: a second call when
already OPERATING converges without recording a duplicate lifecycle transition.

### Deployment failure changes nothing else

A rejected deployment leaves all five Gate-5 quality dimensions PASS, leaves the
`release_candidate` immutable, leaves lifecycle in `BUILDING`, does not auto-kill the
venture, and does not create or alter any investment decision. Retries reuse Gate-4
semantics (same `release_candidate`, new attempt, retained failed history, VERIFIED
proof bound to the successful attempt); changed release intent still requires new
governed authority (Slice 1). No automatic rollback exists — a previous verified
deployment remains historical truth, and any rollback would be a separate governed
action.

### OPERATING ≠ market success

`OPERATING` means a verified deployed runtime per Gate-6 doctrine. It asserts nothing
about customers, WTP, revenue, acquisition, or PMF — Gate 7 Market Runtime remains
separate, and Slice 3 creates no market truth.

## Consequences

- New production module `aidan_core/deploy/state.py` (`verified_deployment`,
  `latest_verified_deployment`, `promote_verified_deployment`); it reuses
  `lifecycle`, `killswitch`, and the Gate-5 quality derivation.
- No new migration, no `deployment_record`, no new capability, no rollback, no
  external provider, no dependencies. Lifecycle vocabulary is unchanged (no
  deployment-run statuses added to it).
