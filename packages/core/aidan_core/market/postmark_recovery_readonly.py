"""READ-ONLY provider reconciliation for a Postmark owner-ingress smoke that failed closed on an
ambiguous send (RECOVERY_REQUIRED) and whose ephemeral governed database no longer exists (Gate 8).

The original run captured only a subset of the canonical correlation in its surviving evidence line
(the ``action_request`` id, venture id, sender, recipient); the ``market_action_spec`` id and
``action_spec_hash`` lived only in the destroyed run database. This entrypoint resolves the one open
question — *does the single authorized message actually exist at Postmark?* — using ONLY Postmark
READ operations:

  * GET /server                          (server identity + Live/Sandbox)
  * GET /messages/outbound?metadata_...  (search by the unique action_request correlation tag)
  * GET /messages/outbound/{id}/details  (full record for exact-identity reconciliation)

It can NEVER send: the transport is wrapped in a read-only seam that does not expose ``send_email``.
It fails closed into RECOVERY_AMBIGUOUS on ANY provider uncertainty (5xx / timeout / malformed /
incomplete coverage / multiple or partially-matching candidates), and only declares
RECOVERY_CONFIRMED_NOT_FOUND on a complete, successful, definitive empty search against the correct
Live server. Sanitized evidence only — no token, no raw provider body.

This resolves an existing RECOVERY_REQUIRED state; it does NOT re-open the governed lifecycle, does
NOT mutate provider state, and does NOT authorize any second send.
"""
from __future__ import annotations

import json
import os
import re

from . import channels as channels_mod
from . import postmark as pm
from . import postmark_smoke_spec as spec
from .postmark import PostmarkReconcileUnknown

CONFIRM_TOKEN = "RUN_POSTMARK_RECOVERY_READONLY"
ACTION_REQUEST_ENV = "POSTMARK_RECOVERY_ACTION_REQUEST_ID"
ACCEPTED_SHA_ENV = "POSTMARK_RECOVERY_ACCEPTED_SHA"

# The correlation-search page cap the transport requests (count=100). A unique action_request UUID
# can correspond to at most one authorized message, so a full page would mean the result set is not
# completely covered -> we must not treat it as a definitive search.
SEARCH_PAGE_CAP = 100

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_action_request_id(value: str) -> str:
    if not value or not _UUID_RE.match(str(value)):
        raise ValueError("action_request_id is not a valid UUID")
    return value


def validate_email(value: str, *, label: str) -> str:
    if not value or not _EMAIL_RE.match(str(value)):
        raise ValueError(f"{label} is not a valid email address")
    return value


class _ReadOnlyPostmark:
    """A read-only seam over a Postmark transport. It delegates ONLY the three read operations the
    recovery needs and deliberately does NOT expose ``send_email`` — so the recovery path cannot
    issue a POST /email even by mistake (a structural guarantee, not merely a convention)."""

    def __init__(self, transport):
        self._t = transport

    def get_server_state(self):
        return self._t.get_server_state()

    def find_outbound_by_correlation(self, correlation):
        return list(self._t.find_outbound_by_correlation(dict(correlation)))

    def get_outbound_message(self, message_id):
        return self._t.get_outbound_message(message_id)


def reconstruct_expected(*, sender: str, recipient: str) -> dict:
    """The exact provider-message identity we CAN reconstruct from the frozen smoke spec plus the
    owner-supplied sender/recipient. Deliberately excludes fields that lived only in the destroyed
    run database (market_action_spec id, action_spec_hash) and the reply-to (derived from those), so
    every field here is independently knowable and deterministically checkable."""
    return {
        "content_hash": channels_mod.content_sha(spec.SMOKE_BODY),
        "recipient_hash": pm._recipient_hash(recipient),
        "subject": spec.SMOKE_SUBJECT,
        "sender": sender,
        "message_stream": spec.MESSAGE_STREAM,
    }


def _matches(msg: dict, expected: dict, action_request_id: str) -> bool:
    """A provider message IS the authorized action's message only if its action_request correlation
    tag AND every reconstructable frozen identity (content, recipient, subject, sender, stream) match
    and it is not Sandboxed. Mirrors the send-path/verifier identity checks over the KNOWN subset."""
    meta = dict(msg.get("Metadata", {}))
    return (str(meta.get("action_request")) == str(action_request_id)
            and channels_mod.content_sha(str(msg.get("TextBody", ""))) == expected["content_hash"]
            and pm._recipient_hash(str(msg.get("To"))) == str(expected["recipient_hash"])
            and str(msg.get("Subject")) == str(expected["subject"])
            and str(msg.get("From")) == str(expected["sender"])
            and str(msg.get("MessageStream")) == str(expected["message_stream"])
            and msg.get("Sandboxed") is not True)


def _summary(msg: dict) -> dict:
    """Sanitized provider evidence for a matched message — identity + state only, never body/PII."""
    meta = dict(msg.get("Metadata", {}))
    return {
        "message_id": str(msg.get("MessageID")),
        "provider_status": msg.get("Status"),
        "message_stream": msg.get("MessageStream"),
        "sandboxed": bool(msg.get("Sandboxed")) if msg.get("Sandboxed") is not None else None,
        "correlation": {"venture": meta.get("venture"), "action_request": meta.get("action_request"),
                        "market_action_spec": meta.get("market_action_spec"),
                        "action_spec_hash": meta.get("action_spec_hash")},
        "matched_fields": ["ACTION_REQUEST", "CONTENT", "RECIPIENT", "SUBJECT", "SENDER",
                           "MESSAGE_STREAM", "NOT_SANDBOXED"],
    }


def run_postmark_recovery_readonly(*, action_request_id: str, server_id: str, sender: str,
                                   recipient: str, token: str = None, transport=None) -> dict:
    """Determine, using ONLY reads, whether the one authorized message exists at Postmark.

    Returns sanitized evidence with ``classification`` in
    {RECOVERY_CONFIRMED_SENT, RECOVERY_CONFIRMED_NOT_FOUND, RECOVERY_AMBIGUOUS}. Any provider
    uncertainty resolves to RECOVERY_AMBIGUOUS (fail closed); NOT_FOUND requires a complete,
    successful, definitive empty search against the correct Live server."""
    validate_action_request_id(action_request_id)
    validate_email(sender, label="sender")
    validate_email(recipient, label="recipient")
    smoke_hash = spec.assert_frozen()   # the frozen message identity we reconstruct against is intact

    raw = transport if transport is not None else pm.PostmarkHttpTransport(
        token if token is not None else os.environ.get(spec.TOKEN_ENV, ""))
    ro = _ReadOnlyPostmark(raw)
    expected = reconstruct_expected(sender=sender, recipient=recipient)

    ev = {"recovery": "gate8-postmark-recovery-readonly", "smoke_spec_hash": smoke_hash,
          "action_request_id": str(action_request_id), "expected_server_id": str(server_id),
          "provider_contact": "UNKNOWN", "classification": "RECOVERY_AMBIGUOUS",
          "sends_issued": 0, "read_only": True,
          "repo_sha": os.environ.get("GITHUB_SHA", "local")}

    # (1) Provider identity read. Uncertainty here means we cannot prove anything -> ambiguous.
    try:
        server = ro.get_server_state()
    except PostmarkReconcileUnknown as exc:
        ev.update(classification="RECOVERY_AMBIGUOUS", reason="provider_unreachable_server",
                  transport_fault=_fault(exc))
        return _finalize(ev)
    ev["provider_contact"] = "OBSERVED"
    server_ok = str(server.server_id) == str(server_id)
    live_ok = str(server.delivery_type) == pm.REQUIRED_DELIVERY_TYPE
    ev["server_identity_match"] = server_ok
    ev["server_is_live"] = live_ok

    # (2) Bounded read-only search by the unique action_request correlation tag (+ per-candidate
    # detail reads). PostmarkReconcileUnknown from any 5xx/timeout/malformed -> fail closed.
    try:
        candidates = ro.find_outbound_by_correlation({"action_request": str(action_request_id)})
    except PostmarkReconcileUnknown as exc:
        ev.update(classification="RECOVERY_AMBIGUOUS", reason="provider_state_unknown",
                  transport_fault=_fault(exc))
        return _finalize(ev)

    ev["candidates_returned"] = len(candidates)
    if len(candidates) >= SEARCH_PAGE_CAP:      # coverage not provably complete -> not definitive
        ev.update(classification="RECOVERY_AMBIGUOUS", reason="search_coverage_incomplete")
        return _finalize(ev)

    matches = [m for m in candidates if _matches(m, expected, action_request_id)]
    mismatched = [m for m in candidates if not _matches(m, expected, action_request_id)]
    ev["matches"] = len(matches)
    ev["correlated_but_mismatched"] = len(mismatched)

    if len(matches) > 1:
        ev.update(classification="RECOVERY_AMBIGUOUS", reason="duplicate_effect")
        return _finalize(ev)
    if len(matches) == 1:
        ev.update(classification="RECOVERY_CONFIRMED_SENT", provider_message=_summary(matches[0]))
        return _finalize(ev)
    # zero fully-matching messages
    if mismatched:
        # a message carries this action_request tag but does not match the frozen identity: we cannot
        # cleanly prove the authorized send occurred OR did not -> stay ambiguous.
        ev.update(classification="RECOVERY_AMBIGUOUS", reason="correlated_but_identity_mismatch")
        return _finalize(ev)
    if not (server_ok and live_ok):
        # a definitive empty search only proves absence for the CORRECT Live server.
        ev.update(classification="RECOVERY_AMBIGUOUS", reason="wrong_or_nonlive_server")
        return _finalize(ev)
    ev.update(classification="RECOVERY_CONFIRMED_NOT_FOUND", reason="definitive_empty_search")
    return _finalize(ev)


def _fault(exc) -> dict:
    """Sanitized transport-fault classification carried on the exception (never a raw body/token)."""
    return {"http_status": getattr(exc, "http_status", None),
            "fault_kind": getattr(exc, "fault_kind", None)}


def _finalize(ev: dict) -> dict:
    token = os.environ.get(spec.TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    return ev


def main() -> int:
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"recovery": "gate8-postmark-recovery-readonly", "result": "CONFIRM_REQUIRED"}))
        return 2
    accepted = os.environ.get(ACCEPTED_SHA_ENV)
    if not accepted:
        print(json.dumps({"recovery": "gate8-postmark-recovery-readonly", "result": "CONFIG_ERROR",
                          "reason": ACCEPTED_SHA_ENV}))
        return 2
    if os.environ.get("GITHUB_SHA") and accepted != os.environ["GITHUB_SHA"]:
        print(json.dumps({"recovery": "gate8-postmark-recovery-readonly", "result": "SHA_MISMATCH"}))
        return 3
    for env in (spec.TOKEN_ENV, ACTION_REQUEST_ENV, spec.SERVER_ID_ENV, spec.SENDER_ENV, spec.RECIPIENT_ENV):
        if not os.environ.get(env):
            print(json.dumps({"recovery": "gate8-postmark-recovery-readonly", "result": "CONFIG_ERROR",
                              "reason": env}))
            return 2
    try:
        ev = run_postmark_recovery_readonly(
            action_request_id=os.environ[ACTION_REQUEST_ENV], server_id=os.environ[spec.SERVER_ID_ENV],
            sender=os.environ[spec.SENDER_ENV], recipient=os.environ[spec.RECIPIENT_ENV])
    except Exception as exc:  # sanitized; never a raw traceback / provider body
        print(json.dumps({"recovery": "gate8-postmark-recovery-readonly", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    print(json.dumps(ev, sort_keys=True))
    # A recovery run "succeeds" (exit 0) when it reaches a definitive terminal classification with no
    # secret leak; an ambiguous outcome is a non-zero (but expected) fail-closed result.
    ok = (ev.get("classification") in ("RECOVERY_CONFIRMED_SENT", "RECOVERY_CONFIRMED_NOT_FOUND")
          and ev.get("secret_leak_check") == "PASS" and ev.get("sends_issued") == 0)
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
