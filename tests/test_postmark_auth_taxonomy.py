"""Postmark auth/config taxonomy, true send-POST accounting, and the zero-send live preflight
(DB-free; stubbed transport / in-memory fake).

Proves: the worker's ACTUAL POST /email counter increments only at the POST boundary (not on worker
entry); GET /server 401/403 is a deterministic AUTH_FAILURE (never ambiguity); the zero-send preflight
authenticates with a single read, cannot send, classifies PASS/AUTH_FAILURE/CONFIG_FAILURE/
PROVIDER_UNAVAILABLE, guards before any provider call, and never leaks the token.
"""
from __future__ import annotations

import json

import pytest

from aidan_core.market import postmark as pm
from aidan_core.market import postmark_live_preflight as pf
from aidan_core.market import postmark_smoke_spec as spec
from aidan_core.market.channels import content_sha
from aidan_core.market.postmark import (PostmarkAuthError, PostmarkHttpTransport,
                                        PostmarkReconcileUnknown, _recipient_hash)

from postmark_fakes import FakePostmarkTransport

SERVER_ID = "20453649"


class FakeHttp:
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


# ==== true send-POST accounting (F) =================================================================
_BODY = "the frozen body"
_EXPECTED = {"correlation": {"action_request": "a1"}, "content_hash": content_sha(_BODY),
             "recipient_hash": _recipient_hash("b@y"), "subject": "s", "reply_to": "x@x",
             "sender": "a@x", "message_stream": "outbound"}


def _send(w):
    return w._send_reconcilably(_EXPECTED, message_stream="outbound", sender="a@x", to="b@y",
                               subject="s", text_body=_BODY, reply_to="x@x",
                               correlation={"action_request": "a1"})


def test_F_send_counter_increments_only_at_post(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/messages/outbound?", (200, {"Messages": []})),
                                                       ("POST", "/email", (200, {"MessageID": "m1"}))]))
    w = pm.PostmarkEmailWorker(PostmarkHttpTransport("tok"), resolver=None, source=None)
    assert w.send_post_calls == 0                       # nothing before the POST
    assert _send(w) == "m1"
    assert w.send_post_calls == 1                       # exactly one ACTUAL POST


def test_F_send_counter_zero_when_prior_message_exists():
    fake = FakePostmarkTransport(server_id=SERVER_ID)
    fake.outbound["prior-1"] = {"MessageID": "prior-1", "Metadata": {"action_request": "a1"},
                                "TextBody": _BODY, "To": "b@y", "Subject": "s", "ReplyTo": "x@x",
                                "From": "a@x", "MessageStream": "outbound", "Sandboxed": False}
    w = pm.PostmarkEmailWorker(fake, resolver=None, source=None)
    assert _send(w) == "prior-1"                        # reconciled to the prior send
    assert w.send_post_calls == 0 and fake._n == 0      # NO new POST issued


# ==== GET /server auth taxonomy at the transport (the exact defect) =================================
def test_get_server_401_is_auth_error_not_ambiguous(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (401, None))]))
    with pytest.raises(PostmarkAuthError) as ei:
        PostmarkHttpTransport("bad-token").get_server_state()
    assert ei.value.http_status == 401 and ei.value.fault_kind == "auth"
    assert not isinstance(ei.value, pm.AmbiguousExternalEffectError)   # deterministic, never recovery


def test_get_server_403_is_auth_error(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (403, None))]))
    with pytest.raises(PostmarkAuthError):
        PostmarkHttpTransport("bad-token").get_server_state()


def test_get_server_503_stays_undetermined(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (503, None))]))
    with pytest.raises(PostmarkReconcileUnknown):     # 5xx is still undetermined (not auth)
        PostmarkHttpTransport("tok").get_server_state()


# ==== zero-send live preflight (I, M, N, J, K, L, O + config/unavailable) ===========================
def _pf(transport):
    return pf.run_postmark_live_preflight(server_id=SERVER_ID, transport=transport)


def test_M_preflight_pass_on_exact_live_server():
    ev = _pf(FakePostmarkTransport(server_id=SERVER_ID, delivery_type="Live"))
    assert ev["classification"] == "PASS" and ev["provider_contact"] == "OBSERVED"
    assert ev["server_identity_match"] and ev["server_is_live"] and ev["sends_issued"] == 0


def test_N_preflight_401_is_auth_failure(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (401, None))]))
    ev = _pf(PostmarkHttpTransport("bad-token"))
    assert ev["classification"] == "AUTH_FAILURE" and ev["provider_contact"] == "UNKNOWN"
    assert ev["transport_fault"]["http_status"] == 401 and ev["sends_issued"] == 0


def test_preflight_wrong_server_is_config_failure():
    ev = _pf(FakePostmarkTransport(server_id="server-OTHER", delivery_type="Live"))
    assert ev["classification"] == "CONFIG_FAILURE" and ev["reason"] == "wrong_server_id"


def test_preflight_non_live_is_config_failure():
    ev = _pf(FakePostmarkTransport(server_id=SERVER_ID, delivery_type="Sandbox"))
    assert ev["classification"] == "CONFIG_FAILURE" and ev["reason"] == "server_not_live"


def test_preflight_5xx_is_provider_unavailable(monkeypatch):
    monkeypatch.setattr(pm, "_http_request", FakeHttp([("GET", "/server", (503, None))]))
    ev = _pf(PostmarkHttpTransport("tok"))
    assert ev["classification"] == "PROVIDER_UNAVAILABLE" and ev["sends_issued"] == 0


def test_I_preflight_cannot_send():
    assert not hasattr(pf._ReadOnlyPostmark(FakePostmarkTransport()), "send_email")

    class _ExplodingSend(FakePostmarkTransport):
        def send_email(self, **kw):
            raise AssertionError("preflight must never send")
    ev = _pf(_ExplodingSend(server_id=SERVER_ID, delivery_type="Live"))   # completes, never sends
    assert ev["classification"] == "PASS" and ev["sends_issued"] == 0


def test_O_no_secret_in_preflight_evidence(monkeypatch):
    monkeypatch.setenv(spec.TOKEN_ENV, "SECRET-SERVER-TOKEN")
    ev = _pf(FakePostmarkTransport(server_id=SERVER_ID, delivery_type="Live"))
    assert "SECRET-SERVER-TOKEN" not in json.dumps(ev) and ev["secret_leak_check"] == "PASS"


def test_K_preflight_bad_confirm_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", "nope")
    assert pf.main() == 2 and "CONFIRM_REQUIRED" in capsys.readouterr().out


def test_J_preflight_sha_mismatch_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", pf.CONFIRM_TOKEN)
    monkeypatch.setenv(pf.ACCEPTED_SHA_ENV, "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert pf.main() == 3 and "SHA_MISMATCH" in capsys.readouterr().out


def test_L_preflight_missing_token_blocks(monkeypatch, capsys):
    monkeypatch.setenv("CONFIRM", pf.CONFIRM_TOKEN)
    monkeypatch.setenv(pf.ACCEPTED_SHA_ENV, "abc")
    monkeypatch.setenv("GITHUB_SHA", "abc")
    monkeypatch.delenv(spec.TOKEN_ENV, raising=False)
    assert pf.main() == 2 and "CONFIG_ERROR" in capsys.readouterr().out
