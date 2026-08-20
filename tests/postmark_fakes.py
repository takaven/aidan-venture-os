"""Deterministic Postmark test doubles (Gate 8 Slice 2) — NO network, NO real credentials.

``FakePostmarkTransport`` holds provider-SIDE state: the worker sends into it, and the verifier
later reads it back independently (never the worker's claim). Credentials are obviously-fake
fixed strings. Recipients are synthetic ``.invalid`` addresses (no real PII).
"""
from __future__ import annotations

import hashlib
from collections import namedtuple

from aidan_core.market import postmark as pm
from aidan_core.market.postmark import PostmarkSource

from factory_fakes import registry_with
from market_fakes import freeze_outreach, market_action, operating_setup

SYNTHETIC_TOKEN = "FAKE-SERVER-TOKEN-do-not-use"
SYNTHETIC_WEBHOOK_USER = "hook"
SYNTHETIC_WEBHOOK_SECRET = "FAKE-WEBHOOK-SECRET"


def default_source(postmark_server_id="server-A", message_stream="outbound"):
    return PostmarkSource(
        postmark_server_id=postmark_server_id, message_stream=message_stream,
        sender="alpha@sender.invalid", default_subject="A quick question",
        inbound_domain="reply.invalid", credential_ref="secret://postmark/alpha",
        webhook_user=SYNTHETIC_WEBHOOK_USER, webhook_secret=SYNTHETIC_WEBHOOK_SECRET)


def basic_auth(user=SYNTHETIC_WEBHOOK_USER, secret=SYNTHETIC_WEBHOOK_SECRET):
    import base64
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


class FakeRecipientResolver:
    """Deterministic, venture-scoped synthetic recipient. Cross-venture -> different address."""

    def resolve(self, venture_id: str, source_instance_ref: str, audience_ref: str) -> str:
        h = hashlib.sha256(f"{venture_id}:{audience_ref}".encode()).hexdigest()[:10]
        return f"lead+{h}@recipients.invalid"


class FakePostmarkTransport:
    """In-memory Postmark provider state. NO network. The worker mutates it via send_email; the
    verifier reads it via find_outbound_by_correlation / get_outbound_message."""

    network_calls = 0  # stays 0 forever — a guard the suite asserts
    # NOTE: no origin_kind flag — a fixture is SIMULATED because it is not a PostmarkHttpTransport,
    # derived inside the trusted origin writer; it can never declare itself REAL_PROVIDER.

    def __init__(self, server_id="server-A", *, delivery_type="Live"):
        self.outbound: dict[str, dict] = {}
        self._n = 0
        self._server_id = server_id           # actual Postmark Server ID this (fake) token belongs to
        self._delivery_type = delivery_type   # 'Live' | 'Sandbox' (Sandbox never reaches a real inbox)

    def send_email(self, *, message_stream, sender, to, subject, text_body, reply_to, metadata) -> str:
        self._n += 1
        message_id = f"pm-{metadata.get('market_action_spec')}-{self._n}"
        self.outbound[message_id] = {
            "MessageID": message_id, "MessageStream": message_stream, "From": sender, "To": to,
            "Subject": subject, "TextBody": text_body, "ReplyTo": reply_to,
            "Sandboxed": self._delivery_type == "Sandbox",
            "Metadata": dict(metadata), "Status": "Sent"}
        return message_id

    def get_outbound_message(self, message_id):
        return self.outbound.get(message_id)

    def get_server_state(self):
        return pm.PostmarkServerState(server_id=self._server_id, delivery_type=self._delivery_type)


def stub_postmark_http(store, server_id="server-A", delivery_type="Live"):
    """A drop-in for ``postmark._http_request`` backed by an in-memory Postmark HTTP API, including
    the server-scoped GET /server (returns the actual Server ID AND DeliveryType this token belongs
    to). Tests patch the LOW-LEVEL network boundary beneath the GENUINE production
    PostmarkHttpTransport — they never subclass/replace the transport object. No network."""
    import json

    def _req(method, url, *, headers, body=None):
        if method == "GET" and url.endswith("/server"):
            return 200, {"ID": server_id, "DeliveryType": delivery_type}   # actual server ID + Live/Sandbox
        if method == "POST" and url.endswith("/email"):
            data = json.loads(body)
            store["_n"] = store.get("_n", 0) + 1
            mid = f"pmhttp-{data['Metadata']['market_action_spec']}-{store['_n']}"
            store[mid] = {"MessageID": mid, "MessageStream": data["MessageStream"], "From": data["From"],
                          "To": [{"Email": data["To"]}], "Subject": data["Subject"],
                          "TextBody": data["TextBody"], "ReplyTo": data.get("ReplyTo"),
                          "Sandboxed": delivery_type == "Sandbox",
                          "Metadata": data["Metadata"], "Status": "Sent"}
            return 200, {"MessageID": mid}
        if method == "GET" and "/messages/outbound/" in url:
            mid = url.split("/messages/outbound/")[1].split("/details")[0]
            rec = store.get(mid)
            return (200, rec) if rec is not None else (404, None)
        return 400, None

    return _req


def install_real_postmark(monkeypatch, *, server_id="server-A", delivery_type="Live"):
    """Return a GENUINE production PostmarkHttpTransport whose HTTP boundary (incl. GET /server) is
    stubbed in-memory; ``server_id``/``delivery_type`` are the actual Postmark server + Live/Sandbox
    the runtime token belongs to."""
    store: dict = {}
    monkeypatch.setattr(pm, "_http_request", stub_postmark_http(store, server_id, delivery_type))
    return pm.PostmarkHttpTransport("STUB-TOKEN-not-used"), store


PostmarkRun = namedtuple("PostmarkRun", "setup action_id spec worker verify transport resolver source")


def postmark_action(conn, setup, *, key, channel_source="server-A", **over):
    a = market_action(conn, setup.venture_id, key=key)
    spec = freeze_outreach(conn, setup, a, channel_kind=pm.POSTMARK_CHANNEL, **over)
    return a, spec


def postmark_run(conn, slug, *, mode="compliant", key=None, source=None, transport=None, resolver=None):
    """OPERATING venture -> freeze postmark-email market action -> worker sends into the fake
    provider -> independent verifier reads provider state -> proof."""
    key = key or slug
    source = source or default_source()
    transport = transport or FakePostmarkTransport()
    resolver = resolver or FakeRecipientResolver()
    setup = operating_setup(conn, slug, key=key)
    a, spec = postmark_action(conn, setup, key=key)
    worker = pm.PostmarkEmailWorker(transport, resolver, source, mode=mode)
    pm.execute_postmark_action(conn, a, registry=registry_with(worker), source=source, resolver=resolver)
    verify = pm.verify_postmark_action(conn, a, transport=transport)
    return PostmarkRun(setup, a, spec, worker, verify, transport, resolver, source)
