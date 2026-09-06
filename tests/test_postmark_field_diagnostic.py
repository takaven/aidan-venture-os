"""Read-only Postmark per-field diagnostic (DB-free; stubbed transport / in-memory fake).

Proves the diagnostic pins the exact mismatching field, distinguishes a pure representational
(CRLF/LF) difference from a semantic one, cannot send, fails closed on provider uncertainty, and
never leaks the raw body or the token.
"""
from __future__ import annotations

import json

from aidan_core.market import postmark as pm
from aidan_core.market import postmark_field_diagnostic as diag
from aidan_core.market import postmark_smoke_spec as spec
from aidan_core.market.postmark import PostmarkHttpTransport

from postmark_fakes import FakePostmarkTransport

ACTION_REQUEST = "3c97e0e6-a32f-4ebc-a863-d7ccfa379e2e"
MESSAGE_ID = "eb8e2cff-b359-481c-8e50-81482c0e7a15"
SERVER_ID = "20453649"
ADDR = "admin@takaven.com"


def _msg(**over):
    m = {"MessageID": MESSAGE_ID, "MessageStream": spec.MESSAGE_STREAM, "From": ADDR, "To": ADDR,
         "Subject": spec.SMOKE_SUBJECT, "TextBody": spec.SMOKE_BODY, "ReplyTo": "reply+x@reply.invalid",
         "Sandboxed": False, "Status": "Sent",
         "Metadata": {"action_request": ACTION_REQUEST, "venture": "v", "market_action_spec": "m",
                      "action_spec_hash": "h"}}
    m.update(over)
    return m


def _fake(msg=None, *, delivery_type="Live"):
    f = FakePostmarkTransport(server_id=SERVER_ID, delivery_type=delivery_type)
    if msg is not None:
        f.outbound[msg["MessageID"]] = msg
    return f


def _run(transport):
    return diag.run_postmark_field_diagnostic(
        action_request_id=ACTION_REQUEST, message_id=MESSAGE_ID, server_id=SERVER_ID,
        sender=ADDR, recipient=ADDR, transport=transport)


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes

    def __call__(self, method, url, *, headers, body=None, timeout=None):
        for m, sub, r in self.routes:
            if method == m and sub in url:
                if isinstance(r, Exception):
                    raise r
                return r
        return (404, None)


# ---- exact match -> every field boolean true ------------------------------------------------------
def test_exact_match_all_true():
    ev = _run(_fake(_msg()))
    assert ev["result"] == "ALL_MATCH" and ev["all_match"] and ev["mismatched_fields"] == []
    f = ev["fields"]
    for k in ("action_request_match", "message_id_match", "recipient_match", "subject_match",
              "sender_match", "message_stream_match", "sandbox_match", "content_match"):
        assert f[k] is True


# ---- the hypothesized real cause: CRLF vs LF is REPRESENTATIONAL, not semantic --------------------
def test_crlf_body_is_representational_only():
    ev = _run(_fake(_msg(TextBody=spec.SMOKE_BODY.replace("\n", "\r\n"))))
    f = ev["fields"]
    assert ev["mismatched_fields"] == ["content_match"]           # only content differs
    assert f["content_match"] is False
    assert f["content_eol_normalized_match"] is True              # reconciles under CRLF->LF
    assert f["content_expected_eol"] == "LF" and f["content_provider_eol"] == "CRLF"


def test_trailing_newline_strip_is_representational():
    ev = _run(_fake(_msg(TextBody=spec.SMOKE_BODY.rstrip("\n"))))
    f = ev["fields"]
    assert f["content_match"] is False and f["content_eol_and_trailing_normalized_match"] is True


# ---- a genuine body change is NOT reconciled by any representational normalization ----------------
def test_semantic_body_change_not_normalizable():
    ev = _run(_fake(_msg(TextBody="A completely different body.\n")))
    f = ev["fields"]
    assert f["content_match"] is False
    assert f["content_eol_normalized_match"] is False and f["content_eol_and_trailing_normalized_match"] is False


# ---- sender display-name formatting: exact fails, parsed mailbox equal ----------------------------
def test_sender_display_name_is_representational():
    ev = _run(_fake(_msg(From="Admin <admin@takaven.com>")))
    f = ev["fields"]
    assert "sender_match" in ev["mismatched_fields"]
    assert f["sender_match"] is False and f["sender_parsed_equal"] is True


# ---- genuine semantic divergences are each pinned correctly ---------------------------------------
def test_wrong_recipient_is_semantic():
    ev = _run(_fake(_msg(To="someone-else@elsewhere.test")))
    f = ev["fields"]
    assert f["recipient_match"] is False and f["recipient_parsed_equal"] is False


def test_wrong_subject_pinned():
    ev = _run(_fake(_msg(Subject="A different subject")))
    assert ev["fields"]["subject_match"] is False and "subject_match" in ev["mismatched_fields"]


def test_wrong_stream_pinned():
    ev = _run(_fake(_msg(MessageStream="broadcast")))
    assert ev["fields"]["message_stream_match"] is False


def test_sandboxed_pinned():
    ev = _run(_fake(_msg(Sandboxed=True)))
    assert ev["fields"]["sandbox_match"] is False and ev["fields"]["sandbox_provider_value"] is True


# ---- message not found (real 404 for the known id) -----------------------------------------------
def test_message_not_found():
    ev = _run(_fake(None))
    assert ev["message_found"] is False and ev["result"] == "MESSAGE_NOT_FOUND"


# ---- the diagnostic seam cannot send -------------------------------------------------------------
def test_seam_cannot_send():
    assert not hasattr(diag._ReadOnlyPostmark(FakePostmarkTransport()), "send_email")
    assert _run(_fake(_msg()))["sends_issued"] == 0


# ---- the raw body never appears in evidence ------------------------------------------------------
def test_raw_body_never_in_evidence():
    marker = "UNIQUE-RAW-BODY-MARKER-zzz"
    ev = _run(_fake(_msg(TextBody=marker + "\n")))
    assert marker not in json.dumps(ev)


# ---- the token never appears in evidence ---------------------------------------------------------
def test_secret_never_in_evidence(monkeypatch):
    monkeypatch.setenv(spec.TOKEN_ENV, "SECRET-SERVER-TOKEN")
    ev = _run(_fake(_msg()))
    assert "SECRET-SERVER-TOKEN" not in json.dumps(ev) and ev["secret_leak_check"] == "PASS"


# ---- provider uncertainty fails closed -----------------------------------------------------------
def test_details_5xx_fails_closed(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([
        ("GET", "/server", (200, {"ID": SERVER_ID, "DeliveryType": "Live"})),
        ("GET", "/messages/outbound/", (503, None))]))
    ev = _run(PostmarkHttpTransport("tok"))
    assert ev["result"] == "PROVIDER_UNAVAILABLE" and ev["transport_fault"]["http_status"] == 503


def test_server_401_is_auth_failure(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (401, None))]))
    ev = _run(PostmarkHttpTransport("bad-token"))
    assert ev["result"] == "AUTH_FAILURE" and ev["provider_contact"] == "UNKNOWN"


# ---- main() guards block before any provider call ------------------------------------------------
def test_bad_confirm_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", "nope")
    assert diag.main() == 2 and "CONFIRM_REQUIRED" in capsys.readouterr().out


def test_sha_mismatch_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", diag.CONFIRM_TOKEN)
    monkeypatch.setenv(diag.ACCEPTED_SHA_ENV, "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert diag.main() == 3 and "SHA_MISMATCH" in capsys.readouterr().out
