"""Bounded, MANUAL, fail-closed FIRST real owner-controlled Postmark market-ingress smoke (Gate 8).

Establishes its own governed OPERATING-venture chain, then sends EXACTLY ONE frozen message to ONE
owner-controlled recipient through the RECONCILABLE consequential send path, and verifies it against
provider state with the deterministic MARKET_ACTION verifier. It sends at most once (max_attempts=1),
never blind-retries an ambiguous send (fails closed into RECOVERY_REQUIRED), never over-promotes
lifecycle, and emits ONE sanitized evidence line (no token, no raw provider payload). The transport is
an injectable seam, so the whole entrypoint is proven deterministically with a fake — no real
Postmark is required for the tests.

Proves the governed SEND boundary only. Does NOT claim inbox placement, human attention, reply,
demand, or commercial validation.
"""
from __future__ import annotations

import json
import os
import re

from . import postmark_smoke_spec as spec

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_recipient(recipient: str) -> str:
    if not recipient or not _EMAIL_RE.match(str(recipient)):
        raise ValueError("recipient is not a valid email address")
    return recipient


class _OwnerResolver:
    """Resolves the single owner-controlled recipient (independent of worker input)."""

    def __init__(self, recipient):
        self._recipient = recipient

    def resolve(self, venture_id, source_instance_ref, audience_ref):
        return self._recipient


def _source(recipient_unused=None):
    from .postmark import PostmarkSource
    return PostmarkSource(
        postmark_server_id=os.environ.get(spec.SERVER_ID_ENV, ""),
        message_stream=spec.MESSAGE_STREAM,                # frozen: outbound only
        sender=os.environ.get(spec.SENDER_ENV, ""),
        default_subject=spec.SMOKE_SUBJECT,                # frozen subject (not worker/env prose)
        inbound_domain=os.environ.get(spec.INBOUND_DOMAIN_ENV, ""),
        credential_ref=os.environ.get(spec.CREDENTIAL_REF_ENV, "postmark-owner-smoke"))


def _gov_count(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (vid,))
        return cur.fetchone()[0]


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        row = cur.fetchone()
    return row[0] if row else None


def _budget(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = 'USD'", (vid,))
        return cur.fetchone()


def run_postmark_ingress_smoke(conn, *, recipient, token=None, transport=None, source=None,
                               actor="market-smoke", slug="gate8-postmark-smoke"):
    """Establish the fixture, govern ONE send, verify against provider state. Returns sanitized
    evidence. Fails closed (RECOVERY_REQUIRED) on any ambiguous send; never over-promotes lifecycle."""
    from .. import execution
    from ..errors import ExecutionBlockedError, InsufficientBudgetError
    from ..factory.workers import WorkerRegistry
    from . import postmark as pm
    from . import postmark_smoke_fixture as fixture

    smoke_hash = spec.assert_frozen()
    validate_recipient(recipient)
    src = source if source is not None else _source()
    resolver = _OwnerResolver(recipient)
    transport = transport if transport is not None else pm.PostmarkHttpTransport(
        token if token is not None else os.environ.get(spec.TOKEN_ENV, ""))

    ev = {"smoke": "gate8-postmark-ingress", "channel": spec.CHANNEL, "smoke_spec_hash": smoke_hash,
          "ceiling": str(spec.CEILING), "recipient_owner_declared": True, "result": None,
          "provider_contact_evidence": "UNKNOWN", "send_effect": "NOT_OBSERVED",
          "promoted_by_send": False, "claims": spec.SMOKE_SPEC["claims"],
          "repo_sha": os.environ.get("GITHUB_SHA", "local")}

    fx = fixture.establish_postmark_smoke_action(conn, slug=slug, actor=actor)
    vid, action_id = fx["venture_id"], fx["action_id"]
    ev["canonical_ids"] = fx
    ev["action_request_id"] = action_id
    ev["lifecycle_before"] = _lifecycle(conn, vid)
    gov_baseline = _gov_count(conn, vid)

    worker = pm.PostmarkEmailWorker(transport, resolver, src)
    reg = WorkerRegistry(); reg.register(worker)
    try:
        r = pm.execute_postmark_action(conn, action_id, registry=reg, source=src, resolver=resolver,
                                       max_attempts=spec.MAX_ATTEMPTS, actor=actor)
    except (InsufficientBudgetError, ExecutionBlockedError) as exc:
        ev["result"] = "RESERVATION_FAILED" if "BUDGET" in str(exc).upper() else "BLOCKED_BEFORE_DISPATCH"
        return _finalize(conn, action_id, vid, ev, gov_baseline, worker)

    ev["failure_class"] = r.failure_class
    status = execution.get_status(conn, action_id)
    if status == "RECOVERY_REQUIRED":
        ev.update(result="RECOVERY_REQUIRED", send_effect="UNKNOWN")
        return _finalize(conn, action_id, vid, ev, gov_baseline, worker)
    if status == "FAILED":
        ev.update(result="FAIL", send_effect="NOT_OBSERVED")
        return _finalize(conn, action_id, vid, ev, gov_baseline, worker)

    # A message id was captured -> verify against provider state (independent of the worker claim).
    with conn.cursor() as cur:
        cur.execute("SELECT external_result_id FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC LIMIT 1", (action_id,))
        row = cur.fetchone()
    ev["message_id"] = row[0] if row else None
    ev["send_effect"] = "OBSERVED" if ev["message_id"] else "UNKNOWN"
    ev["provider_contact_evidence"] = "OBSERVED" if ev["message_id"] else "UNKNOWN"
    out = pm.verify_postmark_action(conn, action_id, transport=transport, actual_cost=spec.CEILING, actor=actor)
    ev["market_verdict"] = "VERIFIED" if out.verified else "REJECTED"
    ev["result"] = "PASS" if execution.get_status(conn, action_id) == "SUCCEEDED" else "FAIL"
    return _finalize(conn, action_id, vid, ev, gov_baseline, worker)


def _finalize(conn, action_id, vid, ev, gov_baseline, worker):
    from .. import execution
    ev["final_status"] = execution.get_status(conn, action_id)
    ev["lifecycle_after"] = _lifecycle(conn, vid)
    ev["lifecycle_over_promoted"] = ev["lifecycle_after"] != ev.get("lifecycle_before")
    ev["governance_deltas"] = _gov_count(conn, vid) - gov_baseline
    ev["send_invocations"] = getattr(worker, "calls", None)
    b = _budget(conn, vid)
    if b is not None:
        ev["reserved"], ev["committed"] = str(b[0]), str(b[1])
    with conn.cursor() as cur:
        cur.execute("SELECT id, verification_type, result, evidence_hash FROM proof_receipt "
                    "WHERE action_request_id = %s ORDER BY created_at DESC LIMIT 1", (action_id,))
        pr = cur.fetchone()
    if pr is not None:
        ev["proof_receipt_id"], ev["proof_verification_type"] = str(pr[0]), pr[1]
        ev["proof_result"], ev["evidence_hash"] = pr[2], pr[3]
    token = os.environ.get(spec.TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    ev["actual_provider_billing"] = "UNKNOWN"
    return ev


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print(json.dumps({"smoke": "gate8-postmark-ingress", "result": "CONFIG_ERROR", "reason": "DATABASE_URL"}))
        return 2
    if os.environ.get("CONFIRM", "") != spec.CONFIRM_TOKEN:
        print(json.dumps({"smoke": "gate8-postmark-ingress", "result": "CONFIRM_REQUIRED"}))
        return 2
    accepted = os.environ.get(spec.ACCEPTED_SHA_ENV)
    if not accepted:
        print(json.dumps({"smoke": "gate8-postmark-ingress", "result": "CONFIG_ERROR", "reason": spec.ACCEPTED_SHA_ENV}))
        return 2
    if os.environ.get("GITHUB_SHA") and accepted != os.environ["GITHUB_SHA"]:
        print(json.dumps({"smoke": "gate8-postmark-ingress", "result": "SHA_MISMATCH"}))
        return 3
    for env in (spec.TOKEN_ENV, spec.RECIPIENT_ENV, spec.SERVER_ID_ENV, spec.SENDER_ENV):
        if not os.environ.get(env):
            print(json.dumps({"smoke": "gate8-postmark-ingress", "result": "CONFIG_ERROR", "reason": env}))
            return 2

    import psycopg
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        ev = run_postmark_ingress_smoke(conn, recipient=os.environ[spec.RECIPIENT_ENV])
    except Exception as exc:  # sanitized; never a raw traceback / provider body
        print(json.dumps({"smoke": "gate8-postmark-ingress", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    finally:
        conn.close()
    print(json.dumps(ev, sort_keys=True))
    ok = (ev.get("result") == "PASS" and ev.get("secret_leak_check") == "PASS"
          and ev.get("governance_deltas") == 0 and ev.get("lifecycle_over_promoted") is False
          and (ev.get("send_invocations") or 0) <= spec.MAX_SENDS)
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
