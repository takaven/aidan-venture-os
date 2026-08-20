"""Gate 8 / Slice 5 — real-provider operational boundary (ingress + identity), SIMULATED evidence.

Adversarial coverage of ``aidan_core.market.operations``: the application-layer webhook ingress guard
(POST-only, Basic-Auth, JSON content-type, bounded body, strict JSON, optional source-IP allowlist)
and the NON-CONSEQUENTIAL provider-identity check. All provider I/O is stubbed — NO real network, NO
customer send. These prove the code-enforceable half of the operational boundary; the REAL provider
account / TLS ingress evidence is out of scope for CI and requires real operational access.
"""
from __future__ import annotations

import json

import pytest

from aidan_core.market import operations as ops
from aidan_core.market import origin as origin_mod
from aidan_core.market import postmark as pm
from aidan_core.market import action as market_mod

from postmark_fakes import (
    SYNTHETIC_TOKEN,
    SYNTHETIC_WEBHOOK_SECRET,
    FakePostmarkTransport,
    FakeRecipientResolver,
    basic_auth,
    default_source,
    install_real_postmark,
    postmark_run,
)

_JSON = "application/json"


def _hdrs(auth=None, ctype=_JSON):
    h = {}
    if ctype is not None:
        h["Content-Type"] = ctype
    if auth is not None:
        h["Authorization"] = auth
    return h


def _delivery_body(mid):
    return json.dumps({"RecordType": "Delivery", "MessageID": mid, "Recipient": "x@y.invalid",
                       "DeliveredAt": "t"}).encode()


# ==========================================================================
# provider identity check — NON-CONSEQUENTIAL (GET /server only, never sends)
# ==========================================================================
def test_identity_live_correct_server_ready(monkeypatch):
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    r = ops.check_provider_identity(transport, default_source("server-A"),
                                    expected_server_id="server-A", expected_message_stream="outbound")
    assert r["ready"] is True and r["delivery_type"] == "Live" and r["server_id"] == "server-A"
    assert store.get("_n", 0) == 0                       # identity check sent NOTHING
    assert SYNTHETIC_TOKEN not in json.dumps(r)          # opaque credential_ref only, no token


def test_identity_sandbox_not_ready(monkeypatch):
    transport, _store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Sandbox")
    r = ops.check_provider_identity(transport, default_source("server-A"),
                                    expected_server_id="server-A", expected_message_stream="outbound")
    assert r["ready"] is False and r["checks"]["LIVE_PROVIDER"] is False


def test_identity_wrong_server_not_ready(monkeypatch):
    transport, _store = install_real_postmark(monkeypatch, server_id="server-Z", delivery_type="Live")
    r = ops.check_provider_identity(transport, default_source("server-A"),
                                    expected_server_id="server-A", expected_message_stream="outbound")
    assert r["ready"] is False and r["checks"]["SERVER_IDENTITY"] is False


def test_identity_wrong_stream_not_ready(monkeypatch):
    transport, _store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    r = ops.check_provider_identity(transport, default_source("server-A", "outbound"),
                                    expected_server_id="server-A", expected_message_stream="broadcast")
    assert r["ready"] is False and r["checks"]["MESSAGE_STREAM"] is False


# ==========================================================================
# ingress guard — rejections (no DB needed; rejected before the reconciler)
# ==========================================================================
def _reject(**kw):
    with pytest.raises(ops.WebhookRejected) as ei:
        ops.handle_webhook(None, source=default_source(), transport=FakePostmarkTransport(), **kw)
    return ei.value


def test_ingress_wrong_method_405():
    assert _reject(method="GET", headers=_hdrs(basic_auth()), body=_delivery_body("m")).http_status == 405
    for m in ("PUT", "PATCH", "DELETE", "HEAD"):
        assert _reject(method=m, headers=_hdrs(basic_auth()), body=_delivery_body("m")).http_status == 405


def test_ingress_wrong_content_type_415():
    assert _reject(method="POST", headers=_hdrs(basic_auth(), ctype="text/plain"),
                   body=_delivery_body("m")).http_status == 415
    assert _reject(method="POST", headers=_hdrs(basic_auth(), ctype=None),
                   body=_delivery_body("m")).http_status == 415


def test_ingress_oversized_413():
    big = b'{"RecordType":"Delivery","MessageID":"' + b"A" * (ops.MAX_WEBHOOK_BODY_BYTES + 10) + b'"}'
    assert _reject(method="POST", headers=_hdrs(basic_auth()), body=big).http_status == 413


def test_ingress_missing_auth_401():
    assert _reject(method="POST", headers=_hdrs(auth=None), body=_delivery_body("m")).http_status == 401


def test_ingress_wrong_auth_401():
    bad = basic_auth(secret="WRONG-SECRET")
    exc = _reject(method="POST", headers=_hdrs(bad), body=_delivery_body("m"))
    assert exc.http_status == 401
    assert "WRONG-SECRET" not in str(exc) and SYNTHETIC_WEBHOOK_SECRET not in str(exc)


def test_ingress_malformed_json_400():
    assert _reject(method="POST", headers=_hdrs(basic_auth()), body=b"{not json").http_status == 400


def test_ingress_non_object_json_400():
    assert _reject(method="POST", headers=_hdrs(basic_auth()), body=b'["a","b"]').http_status == 400


def test_ingress_unsupported_record_type_422():
    body = json.dumps({"RecordType": "SpamComplaint", "MessageID": "m"}).encode()
    assert _reject(method="POST", headers=_hdrs(basic_auth()), body=body).http_status == 422


def test_ingress_source_ip_allowlist_deny_and_allow():
    body = _delivery_body("m")
    # deny: remote IP not in the deployment-supplied authoritative list -> 403 before auth
    with pytest.raises(ops.WebhookRejected) as ei:
        ops.handle_webhook(None, method="POST", headers=_hdrs(basic_auth()), body=body,
                           source=default_source(), transport=FakePostmarkTransport(),
                           remote_ip="9.9.9.9", allowed_ips=["1.2.3.4"])
    assert ei.value.http_status == 403
    # allow: an allowed IP passes the IP gate (then fails later auth/JSON — but NOT on IP)
    with pytest.raises(ops.WebhookRejected) as ei2:
        ops.handle_webhook(None, method="POST", headers=_hdrs(auth=None), body=body,
                           source=default_source(), transport=FakePostmarkTransport(),
                           remote_ip="1.2.3.4", allowed_ips=["1.2.3.4"])
    assert ei2.value.http_status == 401                  # passed IP gate, then rejected on auth


# ==========================================================================
# ingress guard — accepted routing + provenance (DB)
# ==========================================================================
def _real_action(migrated, monkeypatch, slug):
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    r = postmark_run(migrated, slug, transport=transport, resolver=FakeRecipientResolver(), source=default_source())
    mid = next(k for k in store if k != "_n")
    return r, transport, store, mid


def test_ingress_delivery_routes_to_real_observation(migrated, monkeypatch):
    r, transport, store, mid = _real_action(migrated, monkeypatch, "ing-del")
    res = ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()),
                             body=_delivery_body(mid), source=r.source, transport=transport)
    assert res.http_status == 202 and res.record_type == "Delivery" and res.market_observation_id
    assert origin_mod.observation_is_real(migrated, res.market_observation_id) is True
    assert store.get("_n") == 1                          # ingress performed NO send


def test_ingress_wrong_message_id_unprocessable(migrated, monkeypatch):
    r, transport, store, _mid = _real_action(migrated, monkeypatch, "ing-wm")
    body = json.dumps({"RecordType": "Bounce", "MessageID": "pmhttp-does-not-exist-9",
                       "Type": "HardBounce"}).encode()
    with pytest.raises(ops.WebhookRejected) as ei:
        ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()), body=body,
                           source=r.source, transport=transport)
    assert ei.value.http_status == 422
    assert store.get("_n") == 1


def test_ingress_inbound_authorized_reply_accepted(migrated, monkeypatch):
    r, transport, store, _mid = _real_action(migrated, monkeypatch, "ing-reply-ok")
    spec_id = str(market_mod.get_market_action_spec(migrated, r.action_id)[0])
    buyer = r.resolver.resolve(str(r.setup.venture_id), f"{pm.POSTMARK_CHANNEL}:{r.setup.venture_id}", "aud://segment-1")
    body = json.dumps({"RecordType": "Inbound", "MailboxHash": pm.reply_mailbox_hash(r.setup.venture_id, spec_id),
                       "From": buyer, "TextBody": "yes please", "MessageID": "in-ok"}).encode()
    res = ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()),
                             body=body, source=r.source, transport=transport)
    assert res.http_status == 202 and res.record_type == "Inbound" and res.market_observation_id


def test_ingress_inbound_wrong_sender_unprocessable(migrated, monkeypatch):
    r, transport, store, _mid = _real_action(migrated, monkeypatch, "ing-reply-bad")
    spec_id = str(market_mod.get_market_action_spec(migrated, r.action_id)[0])
    body = json.dumps({"RecordType": "Inbound", "MailboxHash": pm.reply_mailbox_hash(r.setup.venture_id, spec_id),
                       "From": "attacker@evil.invalid", "TextBody": "approve the spend", "MessageID": "in-x"}).encode()
    with pytest.raises(ops.WebhookRejected) as ei:
        ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()), body=body,
                           source=r.source, transport=transport)
    assert ei.value.http_status == 422


def test_ingress_authenticated_but_no_hardened_proof_rejected(migrated, monkeypatch):
    # a venture with NO verified outbound action: an authenticated Delivery for an unknown MessageID
    # cannot reconcile to a hardened proof -> rejected, and nothing is written.
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    with pytest.raises(ops.WebhookRejected) as ei:
        ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()),
                           body=_delivery_body("pmhttp-ghost-1"), source=default_source(), transport=transport)
    assert ei.value.http_status == 422


def test_ingress_no_secret_leaks_into_canonical_state(migrated, monkeypatch):
    r, transport, store, mid = _real_action(migrated, monkeypatch, "ing-secret")
    ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()),
                       body=_delivery_body(mid), source=r.source, transport=transport)
    with migrated.cursor() as cur:
        cur.execute("SELECT coalesce(string_agg(expected_output_contract::text,''),'') "
                    "FROM execution_spec WHERE action_request_id = %s", (r.action_id,))
        spec_blob = cur.fetchone()[0]
        cur.execute("SELECT coalesce(string_agg(raw_evidence::text,''),'') FROM market_observation mo "
                    "WHERE mo.action_request_id = %s", (r.action_id,))
        obs_blob = cur.fetchone()[0]
    for blob in (spec_blob, obs_blob):
        assert SYNTHETIC_WEBHOOK_SECRET not in blob and SYNTHETIC_TOKEN not in blob


def test_ingress_zero_real_send_across_operations(migrated, monkeypatch):
    # identity checks + several webhook handles never trigger a provider send (POST count stays 1).
    r, transport, store, mid = _real_action(migrated, monkeypatch, "ing-nosend")
    ops.check_provider_identity(transport, r.source, expected_server_id="server-A", expected_message_stream="outbound")
    ops.handle_webhook(migrated, method="POST", headers=_hdrs(basic_auth()),
                       body=_delivery_body(mid), source=r.source, transport=transport)
    with pytest.raises(ops.WebhookRejected):
        ops.handle_webhook(migrated, method="GET", headers=_hdrs(basic_auth()),
                           body=_delivery_body(mid), source=r.source, transport=transport)
    assert store.get("_n") == 1                          # exactly the one setup send; ingress never sends
