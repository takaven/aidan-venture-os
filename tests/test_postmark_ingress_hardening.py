"""Postmark reconciliation fail-closed + explicit HTTP timeout (DB-free; stubbed transport).

Proves matrix rows A-D, G, H, K, L at the transport boundary: provider uncertainty (5xx / timeout /
malformed) is NEVER an empty 'no prior send' result, only a valid HTTP 200 is definitive, the HTTP
timeout is actually passed to urllib, and the send request is single-recipient with no CC/BCC/attach.
"""
from __future__ import annotations

import json

import pytest

from aidan_core.market import postmark as pm
from aidan_core.market.postmark import (POSTMARK_HTTP_TIMEOUT_SECONDS, PostmarkHttpTransport,
                                        PostmarkReconcileUnknown, PostmarkSendRejected)

CORR = {"venture": "v1", "market_action_spec": "mas1", "action_request": "a1", "action_spec_hash": "h1"}


class FakeHttp:
    """Routes (method, url-substring) -> FlyResponse-like (status, data) or an Exception to raise."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, *, headers, body=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body, "timeout": timeout})
        for m, sub, r in self.routes:
            if method == m and sub in url:
                if isinstance(r, Exception):
                    raise r
                return r
        return (404, None)


def _t():
    return PostmarkHttpTransport(server_token="tok-not-real")


# ---- A: HTTP 200 + definitive no-match -> empty (true NO_MATCH) -----------------------
def test_A_search_200_empty_is_definitive_no_match(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/messages/outbound?", (200, {"Messages": []}))]))
    assert _t().find_outbound_by_correlation(CORR) == []


def test_A2_search_200_match_returns_details(monkeypatch):
    details = {"MessageID": "m1", "Metadata": CORR, "From": "a@x", "To": "b@y", "Subject": "s",
               "TextBody": "t", "MessageStream": "outbound", "Sandboxed": False}
    monkeypatch.setattr(pm, "_http_request", FakeHttp([
        ("GET", "/messages/outbound?", (200, {"Messages": [{"MessageID": "m1"}]})),
        ("GET", "/messages/outbound/m1/details", (200, details))]))
    out = _t().find_outbound_by_correlation(CORR)
    assert len(out) == 1 and out[0]["MessageID"] == "m1"


# ---- B: 5xx -> UNKNOWN, never empty ---------------------------------------------------
def test_B_search_5xx_is_unknown_never_empty(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/messages/outbound?", (503, None))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().find_outbound_by_correlation(CORR)


def test_B_details_5xx_is_unknown(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([
        ("GET", "/messages/outbound?", (200, {"Messages": [{"MessageID": "m1"}]})),
        ("GET", "/messages/outbound/m1/details", (503, None))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().find_outbound_by_correlation(CORR)


# ---- C: timeout/network -> UNKNOWN ----------------------------------------------------
def test_C_transport_error_is_unknown(monkeypatch):
    monkeypatch.setattr(pm, "_http_request",
                        FakeHttp([("GET", "/messages/outbound?", PostmarkReconcileUnknown("timeout"))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().find_outbound_by_correlation(CORR)


# ---- D: malformed payload -> UNKNOWN --------------------------------------------------
def test_D_malformed_search_is_unknown(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/messages/outbound?", (200, {"nope": 1}))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().find_outbound_by_correlation(CORR)


def test_D_200_none_body_is_unknown(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/messages/outbound?", (200, None))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().find_outbound_by_correlation(CORR)


# ---- G: known MessageID reconciles correctly -----------------------------------------
def test_G_get_outbound_404_is_definitive_none(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/details", (404, None))]))
    assert _t().get_outbound_message("m1") is None       # a real 404 = definitive no-record


def test_G_get_outbound_5xx_is_unknown(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/details", (500, None))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().get_outbound_message("m1")


def test_server_state_5xx_is_unknown(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (503, None))]))
    with pytest.raises(PostmarkReconcileUnknown):
        _t().get_server_state()


# ---- send_email: 4xx rejection vs 5xx/None ambiguous ---------------------------------
def test_send_4xx_is_rejected(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("POST", "/email", (422, {"Message": "bad"}))]))
    with pytest.raises(PostmarkSendRejected):
        _t().send_email(message_stream="outbound", sender="a@x", to="b@y", subject="s",
                        text_body="t", reply_to="r@x", metadata=CORR)


def test_send_5xx_is_ambiguous(monkeypatch):
    from aidan_core.errors import AmbiguousExternalEffectError
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("POST", "/email", (503, None))]))
    with pytest.raises(AmbiguousExternalEffectError):
        _t().send_email(message_stream="outbound", sender="a@x", to="b@y", subject="s",
                        text_body="t", reply_to="r@x", metadata=CORR)


# ---- K/L: send request is single-recipient, no CC/BCC/attachments --------------------
def test_K_L_send_request_shape(monkeypatch):
    fake = FakeHttp([("POST", "/email", (200, {"MessageID": "m1"}))])
    monkeypatch.setattr(pm, "_http_request", fake)
    _t().send_email(message_stream="outbound", sender="a@x", to="b@y", subject="s",
                    text_body="t", reply_to="r@x", metadata=CORR)
    body = json.loads(fake.calls[0]["body"])
    assert isinstance(body["To"], str)                    # single recipient, not a list/batch
    assert "Cc" not in body and "Bcc" not in body and "Attachments" not in body
    assert set(body) == {"From", "To", "Subject", "TextBody", "ReplyTo", "Metadata", "MessageStream"}


# ---- H: explicit HTTP timeout actually reaches the transport (urllib) -----------------
def test_H_timeout_reaches_urllib(monkeypatch):
    import urllib.request
    captured = {}

    class _Resp:
        status = 200
        def read(self): return b'{"ID":"s","DeliveryType":"Live"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    _t().get_server_state()                                # calls the REAL _http_request
    assert captured["timeout"] == POSTMARK_HTTP_TIMEOUT_SECONDS and POSTMARK_HTTP_TIMEOUT_SECONDS <= 30
