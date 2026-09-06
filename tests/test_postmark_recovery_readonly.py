"""Read-only Postmark recovery reconciliation (DB-free; stubbed transport / in-memory fake).

Proves the recovery resolves a RECOVERY_REQUIRED owner-ingress send using ONLY provider reads: it
NEVER sends, fails closed into RECOVERY_AMBIGUOUS on any provider uncertainty, confirms SENT on
exactly one identity-matching message, and only declares NOT_FOUND on a complete, successful,
definitive empty search against the correct Live server. Sanitized evidence only.
"""
from __future__ import annotations

import copy
import json

import pytest

from aidan_core.market import postmark as pm
from aidan_core.market import postmark_recovery_readonly as rec
from aidan_core.market import postmark_smoke_spec as spec
from aidan_core.market.postmark import PostmarkReconcileUnknown

from postmark_fakes import FakePostmarkTransport

ACTION_REQUEST = "0bc25392-ce46-4b33-895a-147a3c1fb88e"
SERVER_ID = "server-A"
SENDER = "owner@owner.invalid"
RECIPIENT = "owner@owner.invalid"


def _matching_message(*, message_id="pm-real-1", action_request=ACTION_REQUEST, **over):
    md = {"venture": "v1", "market_action_spec": "mas1", "action_request": action_request,
          "action_spec_hash": "h1"}
    msg = {"MessageID": message_id, "MessageStream": spec.MESSAGE_STREAM, "From": SENDER,
           "To": RECIPIENT, "Subject": spec.SMOKE_SUBJECT, "TextBody": spec.SMOKE_BODY,
           "ReplyTo": "reply+x@reply.invalid", "Sandboxed": False, "Metadata": md, "Status": "Sent"}
    msg.update(over)
    return msg


def _fake_with(*msgs, server_id=SERVER_ID, delivery_type="Live"):
    fake = FakePostmarkTransport(server_id=server_id, delivery_type=delivery_type)
    for m in msgs:
        fake.outbound[m["MessageID"]] = m
    return fake


def _run(transport, *, action_request=ACTION_REQUEST, server_id=SERVER_ID, sender=SENDER,
         recipient=RECIPIENT):
    return rec.run_postmark_recovery_readonly(
        action_request_id=action_request, server_id=server_id, sender=sender,
        recipient=recipient, transport=transport)


class FakeHttp:
    """Routes (method, url-substring) -> (status, data) or an Exception to raise."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, *, headers, body=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body})
        for m, sub, r in self.routes:
            if method == m and sub in url:
                if isinstance(r, Exception):
                    raise r
                return r
        return (404, None)


# ---- CONFIRMED_SENT: exactly one identity-matching message; zero sends; provider observed --------
def test_confirmed_sent_one_message():
    fake = _fake_with(_matching_message())
    ev = _run(fake)
    assert ev["classification"] == "RECOVERY_CONFIRMED_SENT"
    assert ev["provider_message"]["message_id"] == "pm-real-1"
    assert ev["provider_contact"] == "OBSERVED"
    assert ev["sends_issued"] == 0 and fake._n == 0      # NO send POST issued at all
    assert ev["matches"] == 1 and ev["secret_leak_check"] == "PASS"


# ---- CONFIRMED_NOT_FOUND: definitive successful empty search on the correct Live server ----------
def test_confirmed_not_found_empty_search():
    fake = _fake_with()   # provider holds no message
    ev = _run(fake)
    assert ev["classification"] == "RECOVERY_CONFIRMED_NOT_FOUND"
    assert ev["candidates_returned"] == 0 and ev["reason"] == "definitive_empty_search"
    assert fake._n == 0


# ---- AMBIGUOUS: two plausible messages -> duplicate effect ----------------------------------------
def test_multiple_matches_ambiguous():
    fake = _fake_with(_matching_message(message_id="pm-1"), _matching_message(message_id="pm-2"))
    ev = _run(fake)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS" and ev["reason"] == "duplicate_effect"


# ---- AMBIGUOUS: a message carries the action_request tag but the frozen identity does not match ---
def test_correlated_but_identity_mismatch_ambiguous():
    fake = _fake_with(_matching_message(Subject="A different subject"))
    ev = _run(fake)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS"
    assert ev["reason"] == "correlated_but_identity_mismatch"


# ---- AMBIGUOUS: a matching message but on a Sandbox server (never a real inbox) -------------------
def test_sandboxed_message_not_matched():
    fake = _fake_with(_matching_message(Sandboxed=True), delivery_type="Sandbox")
    ev = _run(fake)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS"     # sandboxed msg fails _matches -> not clean


# ---- AMBIGUOUS: provider uncertainty (5xx / timeout / malformed) never resolves ------------------
def _http_transport(monkeypatch, routes):
    monkeypatch.setattr(pm, "_http_request", FakeHttp(routes))
    return pm.PostmarkHttpTransport("tok-not-real")


def test_search_5xx_is_ambiguous(monkeypatch):
    t = _http_transport(monkeypatch, [
        ("GET", "/server", (200, {"ID": SERVER_ID, "DeliveryType": "Live"})),
        ("GET", "/messages/outbound?", (503, None))])
    ev = _run(t)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS" and ev["reason"] == "provider_state_unknown"
    assert ev["transport_fault"]["http_status"] == 503 and ev["transport_fault"]["fault_kind"] == "http_status"


def test_search_timeout_is_ambiguous(monkeypatch):
    t = _http_transport(monkeypatch, [
        ("GET", "/server", (200, {"ID": SERVER_ID, "DeliveryType": "Live"})),
        ("GET", "/messages/outbound?", PostmarkReconcileUnknown("timeout", fault_kind="timeout"))])
    ev = _run(t)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS"
    assert ev["transport_fault"]["fault_kind"] == "timeout"


def test_search_malformed_is_ambiguous(monkeypatch):
    t = _http_transport(monkeypatch, [
        ("GET", "/server", (200, {"ID": SERVER_ID, "DeliveryType": "Live"})),
        ("GET", "/messages/outbound?", (200, {"nope": 1}))])   # 200 but no Messages array -> UNKNOWN
    ev = _run(t)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS"
    assert ev["transport_fault"]["fault_kind"] == "malformed"


def test_server_unreachable_is_ambiguous(monkeypatch):
    t = _http_transport(monkeypatch, [("GET", "/server", (503, None))])
    ev = _run(t)
    assert ev["classification"] == "RECOVERY_AMBIGUOUS" and ev["reason"] == "provider_unreachable_server"
    assert ev["provider_contact"] == "UNKNOWN"


# ---- AMBIGUOUS: empty search but the server identity does not match (searched the wrong server) ---
def test_wrong_server_empty_is_ambiguous():
    fake = _fake_with(server_id="server-OTHER")
    ev = _run(fake)   # expected SERVER_ID != server-OTHER
    assert ev["classification"] == "RECOVERY_AMBIGUOUS" and ev["reason"] == "wrong_or_nonlive_server"


# ---- the recovery CANNOT send: the read-only seam does not expose send_email ----------------------
def test_readonly_wrapper_has_no_send_email():
    ro = rec._ReadOnlyPostmark(FakePostmarkTransport())
    assert not hasattr(ro, "send_email")


def test_recovery_never_calls_send_even_if_available():
    class _ExplodingSend(FakePostmarkTransport):
        def send_email(self, **kw):
            raise AssertionError("recovery must never send")
    fake = _ExplodingSend(server_id=SERVER_ID)
    fake.outbound["pm-real-1"] = _matching_message()
    ev = _run(fake)                                        # completes without ever touching send_email
    assert ev["classification"] == "RECOVERY_CONFIRMED_SENT" and ev["sends_issued"] == 0


# ---- main() guards block before ANY provider read ------------------------------------------------
def test_bad_confirm_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", "nope")
    assert rec.main() == 2 and "CONFIRM_REQUIRED" in capsys.readouterr().out


def test_accepted_sha_mismatch_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", rec.CONFIRM_TOKEN)
    monkeypatch.setenv(rec.ACCEPTED_SHA_ENV, "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert rec.main() == 3 and "SHA_MISMATCH" in capsys.readouterr().out


def test_missing_action_request_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", rec.CONFIRM_TOKEN)
    monkeypatch.setenv(rec.ACCEPTED_SHA_ENV, "abc")
    monkeypatch.setenv("GITHUB_SHA", "abc")
    monkeypatch.setenv(spec.TOKEN_ENV, "tok")
    monkeypatch.delenv(rec.ACTION_REQUEST_ENV, raising=False)
    assert rec.main() == 2 and "CONFIG_ERROR" in capsys.readouterr().out


# ---- the token never appears in the sanitized evidence -------------------------------------------
def test_no_secret_in_evidence(monkeypatch):
    monkeypatch.setenv(spec.TOKEN_ENV, "SECRET-SERVER-TOKEN")
    ev = _run(_fake_with(_matching_message()))
    assert "SECRET-SERVER-TOKEN" not in json.dumps(ev) and ev["secret_leak_check"] == "PASS"


# ---- spec tamper fails closed BEFORE any provider read -------------------------------------------
def test_spec_tamper_blocks(monkeypatch):
    fake = _fake_with(_matching_message())
    tampered = copy.deepcopy(spec.SMOKE_SPEC)
    tampered["subject"] = "Tampered subject"
    monkeypatch.setattr(spec, "SMOKE_SPEC", tampered)
    with pytest.raises(spec.PostmarkSmokeSpecMismatch):
        _run(fake)
    assert fake._n == 0        # never sent, never even read past the frozen-spec gate


# ---- a bad action_request id is rejected before any provider read --------------------------------
def test_bad_action_request_id_rejected():
    fake = _fake_with(_matching_message())
    with pytest.raises(ValueError):
        _run(fake, action_request="not-a-uuid")
    assert fake._n == 0
