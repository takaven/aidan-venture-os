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


def default_source(server_id="server-A"):
    return PostmarkSource(
        server_id=server_id, sender="alpha@sender.invalid", default_subject="A quick question",
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

    def __init__(self):
        self.outbound: dict[str, dict] = {}
        self._n = 0

    def send_email(self, *, server_id, sender, to, subject, text_body, reply_to, metadata) -> str:
        self._n += 1
        message_id = f"pm-{metadata.get('market_action_spec')}-{self._n}"
        self.outbound[message_id] = {
            "MessageID": message_id, "ServerID": server_id, "From": sender, "To": to,
            "Subject": subject, "TextBody": text_body, "ReplyTo": reply_to,
            "Metadata": dict(metadata), "Status": "Sent"}
        return message_id

    def get_outbound_message(self, message_id):
        return self.outbound.get(message_id)

    def find_outbound_by_correlation(self, correlation):
        keys = ("venture", "market_action_spec", "action_request", "action_spec_hash")
        return [r for r in self.outbound.values()
                if all(str(r["Metadata"].get(k)) == str(correlation.get(k)) for k in keys)]

    def check_webhook_auth(self, auth_header):
        return auth_header == basic_auth()


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
    pm.execute_postmark_action(conn, a, registry=registry_with(worker), source=source)
    verify = pm.verify_postmark_action(conn, a, transport=transport, resolver=resolver, source=source)
    return PostmarkRun(setup, a, spec, worker, verify, transport, resolver, source)
