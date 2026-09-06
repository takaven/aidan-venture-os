"""READ-ONLY per-field identity diagnostic for a Postmark message that was really sent but whose
independent MARKET_ACTION verification REJECTED (Gate 8).

The owner-ingress send (run 34025866162) produced a real provider message (MessageID
eb8e2cff-…) that the deterministic verifier rejected, and the read-only recovery confirmed the
message EXISTS but does not match the frozen identity (correlated_but_identity_mismatch). This
entrypoint fetches that exact message with GET reads ONLY and emits a SANITIZED per-field comparison
so the mismatching field can be pinned DETERMINISTICALLY before any verifier change.

It can NEVER send (the transport is wrapped read-only, no send_email), issues NO POST /email, and
mutates nothing. It emits NO raw email body, NO token, NO raw provider payload — only booleans,
hashes, lengths, line-ending classes, parsed-address equality, and field types.
"""
from __future__ import annotations

import json
import os
import re

from . import postmark_smoke_spec as spec
from . import postmark as pm
from .channels import content_sha
from .postmark import (PostmarkAuthError, PostmarkReconcileUnknown, REQUIRED_DELIVERY_TYPE,
                       _normalize_email, _recipient_hash)
from .postmark_recovery_readonly import _ReadOnlyPostmark

CONFIRM_TOKEN = "RUN_POSTMARK_FIELD_DIAGNOSTIC"
ACTION_REQUEST_ENV = "POSTMARK_DIAG_ACTION_REQUEST_ID"
MESSAGE_ID_ENV = "POSTMARK_DIAG_MESSAGE_ID"
ACCEPTED_SHA_ENV = "POSTMARK_DIAG_ACCEPTED_SHA"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_uuid(value, *, label):
    if not value or not _UUID_RE.match(str(value)):
        raise ValueError(f"{label} is not a valid UUID")
    return value


def _validate_email(value, *, label):
    if not value or not _EMAIL_RE.match(str(value)):
        raise ValueError(f"{label} is not a valid email address")
    return value


def _norm_eol(s):
    """Canonicalize line endings CRLF/CR -> LF (a pure representational transform)."""
    return s.replace("\r\n", "\n").replace("\r", "\n") if s is not None else s


def _eol_class(s) -> str:
    if s is None:
        return "null"
    bare = s.replace("\r\n", "")
    has_crlf, has_lf, has_cr = ("\r\n" in s), ("\n" in bare), ("\r" in bare)
    if not (has_crlf or has_lf or has_cr):
        return "none"
    if has_crlf and not has_lf and not has_cr:
        return "CRLF"
    if has_lf and not has_crlf and not has_cr:
        return "LF"
    if has_cr and not has_crlf and not has_lf:
        return "CR"
    return "mixed"


def _content_diag(provider_text: str) -> dict:
    """Sanitized content diagnostics — hashes/lengths/eol only, NEVER the raw body."""
    expected = spec.SMOKE_BODY
    exp_hash, prov_hash = content_sha(expected), content_sha(str(provider_text or ""))
    eol_norm_match = (provider_text is not None
                      and content_sha(_norm_eol(str(provider_text))) == content_sha(_norm_eol(expected)))
    full_norm_match = (provider_text is not None
                       and content_sha(_norm_eol(str(provider_text)).rstrip())
                       == content_sha(_norm_eol(expected).rstrip()))
    return {
        "content_match": exp_hash == prov_hash,
        "content_expected_hash": exp_hash,
        "content_provider_hash": prov_hash,
        "content_expected_len": len(expected),
        "content_provider_len": (len(str(provider_text)) if provider_text is not None else None),
        "content_provider_type": type(provider_text).__name__,
        "content_expected_eol": _eol_class(expected),
        "content_provider_eol": _eol_class(provider_text),
        "content_eol_normalized_match": bool(eol_norm_match),          # CRLF/LF representational only
        "content_eol_and_trailing_normalized_match": bool(full_norm_match),
    }


def run_postmark_field_diagnostic(*, action_request_id: str, message_id: str, server_id: str,
                                  sender: str, recipient: str, token: str = None, transport=None) -> dict:
    """Fetch the exact known message with reads only and emit a sanitized per-field comparison."""
    _validate_uuid(action_request_id, label="action_request_id")
    _validate_uuid(message_id, label="message_id")
    _validate_email(sender, label="sender")
    _validate_email(recipient, label="recipient")
    smoke_hash = spec.assert_frozen()

    raw = transport if transport is not None else pm.PostmarkHttpTransport(
        token if token is not None else os.environ.get(spec.TOKEN_ENV, ""))
    ro = _ReadOnlyPostmark(raw)                 # exposes reads only; NO send_email

    ev = {"diagnostic": "gate8-postmark-field-diagnostic", "smoke_spec_hash": smoke_hash,
          "action_request_id": str(action_request_id), "known_message_id": str(message_id),
          "expected_server_id": str(server_id), "provider_contact": "UNKNOWN",
          "message_found": False, "sends_issued": 0, "read_only": True,
          "repo_sha": os.environ.get("GITHUB_SHA", "local")}

    try:
        server = ro.get_server_state()
    except PostmarkAuthError as exc:
        ev.update(result="AUTH_FAILURE", transport_fault={"http_status": getattr(exc, "http_status", None),
                                                          "fault_kind": "auth"})
        return _finalize(ev)
    except PostmarkReconcileUnknown as exc:
        ev.update(result="PROVIDER_UNAVAILABLE",
                  transport_fault={"http_status": getattr(exc, "http_status", None),
                                   "fault_kind": getattr(exc, "fault_kind", None)})
        return _finalize(ev)
    ev["provider_contact"] = "OBSERVED"
    ev["server_identity_match"] = str(server.server_id) == str(server_id)
    ev["server_is_live"] = str(server.delivery_type) == REQUIRED_DELIVERY_TYPE

    try:
        msg = ro.get_outbound_message(message_id)          # fetch the EXACT known message id
    except (PostmarkAuthError, PostmarkReconcileUnknown) as exc:
        ev.update(result="PROVIDER_UNAVAILABLE",
                  transport_fault={"http_status": getattr(exc, "http_status", None),
                                   "fault_kind": getattr(exc, "fault_kind", None)})
        return _finalize(ev)
    if msg is None:
        ev.update(result="MESSAGE_NOT_FOUND")              # a real 404 for the known id
        return _finalize(ev)

    ev["message_found"] = True
    meta = dict(msg.get("Metadata", {}))
    to_field, from_field = msg.get("To"), msg.get("From")
    fields = {
        "action_request_match": str(meta.get("action_request")) == str(action_request_id),
        "message_id_match": str(msg.get("MessageID")) == str(message_id),
        "recipient_match": _recipient_hash(str(to_field)) == _recipient_hash(recipient),
        "recipient_parsed_equal": _normalize_email(to_field) == _normalize_email(recipient),
        "recipient_provider_type": type(to_field).__name__,
        "subject_match": str(msg.get("Subject")) == spec.SMOKE_SUBJECT,
        "subject_expected_len": len(spec.SMOKE_SUBJECT),
        "subject_provider_len": (len(str(msg.get("Subject"))) if msg.get("Subject") is not None else None),
        "subject_normalized_match": (msg.get("Subject") is not None
                                     and str(msg.get("Subject")).strip() == spec.SMOKE_SUBJECT.strip()),
        "sender_match": str(from_field) == str(sender),
        "sender_parsed_equal": _normalize_email(from_field) == _normalize_email(sender),
        "sender_provider_type": type(from_field).__name__,
        "message_stream_match": str(msg.get("MessageStream")) == str(spec.MESSAGE_STREAM),
        "message_stream_provider": msg.get("MessageStream"),      # non-secret stream id
        "sandbox_match": msg.get("Sandboxed") is not True,
        "sandbox_provider_value": msg.get("Sandboxed"),
    }
    fields.update(_content_diag(msg.get("TextBody")))
    ev["fields"] = fields
    # a semantic-vs-representational summary: which hard fields fail, and whether content reconciles
    # under a pure representational (line-ending) normalization only.
    hard_fail = [k for k in ("action_request_match", "message_id_match", "recipient_match",
                             "subject_match", "sender_match", "message_stream_match", "sandbox_match",
                             "content_match") if fields.get(k) is False]
    ev["mismatched_fields"] = hard_fail
    ev["all_match"] = not hard_fail
    ev["result"] = "ALL_MATCH" if not hard_fail else "MISMATCH"
    return _finalize(ev)


def _finalize(ev: dict) -> dict:
    token = os.environ.get(spec.TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    return ev


def main() -> int:
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"diagnostic": "gate8-postmark-field-diagnostic", "result": "CONFIRM_REQUIRED"}))
        return 2
    accepted = os.environ.get(ACCEPTED_SHA_ENV)
    if not accepted:
        print(json.dumps({"diagnostic": "gate8-postmark-field-diagnostic", "result": "CONFIG_ERROR",
                          "reason": ACCEPTED_SHA_ENV}))
        return 2
    if os.environ.get("GITHUB_SHA") and accepted != os.environ["GITHUB_SHA"]:
        print(json.dumps({"diagnostic": "gate8-postmark-field-diagnostic", "result": "SHA_MISMATCH"}))
        return 3
    for env in (spec.TOKEN_ENV, ACTION_REQUEST_ENV, MESSAGE_ID_ENV, spec.SERVER_ID_ENV,
                spec.SENDER_ENV, spec.RECIPIENT_ENV):
        if not os.environ.get(env):
            print(json.dumps({"diagnostic": "gate8-postmark-field-diagnostic", "result": "CONFIG_ERROR",
                              "reason": env}))
            return 2
    try:
        ev = run_postmark_field_diagnostic(
            action_request_id=os.environ[ACTION_REQUEST_ENV], message_id=os.environ[MESSAGE_ID_ENV],
            server_id=os.environ[spec.SERVER_ID_ENV], sender=os.environ[spec.SENDER_ENV],
            recipient=os.environ[spec.RECIPIENT_ENV])
    except Exception as exc:  # sanitized; never a raw traceback / provider body
        print(json.dumps({"diagnostic": "gate8-postmark-field-diagnostic", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    print(json.dumps(ev, sort_keys=True))
    return 0 if ev.get("secret_leak_check") == "PASS" and ev.get("sends_issued") == 0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
