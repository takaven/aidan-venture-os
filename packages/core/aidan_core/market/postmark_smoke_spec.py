"""FROZEN one-send owner-controlled Postmark market-ingress smoke contract (Gate 8).

The first real Postmark send is a single, immutable, preregistered boundary smoke: exactly ONE
message, to ONE owner-controlled recipient, on the outbound stream, no CC/BCC/attachments, bounded
to USD 0.01, verified by the deterministic MARKET_ACTION verifier against provider state. Every
behavioural bound (subject, body, channel, stream, ceiling, one-send/one-attempt) is frozen here;
the owner supplies only the provider identity (server/sender/stream/domain, from env secrets) and
the recipient. A canonical spec hash fails closed before any send if these are tampered.

This smoke proves the market-ingress SEND boundary — it does NOT claim inbox placement, human
attention, reply, demand, or any commercial validation, and it does NOT change lifecycle.
"""
from __future__ import annotations

from decimal import Decimal

from ..actions import canonical_payload_hash

CONFIRM_TOKEN = "RUN_OWNER_POSTMARK_INGRESS_SMOKE"
CHANNEL = "postmark-email"
MESSAGE_STREAM = "outbound"          # transactional/outbound stream only
CEILING = Decimal("0.01")            # conservative committed-spend ceiling
MAX_SENDS = 1
MAX_ATTEMPTS = 1
SMOKE_SUBJECT = "AIDAN Gate-8 market-ingress boundary smoke"
SMOKE_BODY = ("This is a one-time AIDAN Gate-8 market-ingress boundary smoke sent to an "
              "owner-controlled address. It proves the governed send boundary only.\n")

# Env the owner sets: provider identity (non-secret) + the recipient (owner-controlled) + the token
# (SECRET) + the accepted-main SHA.
TOKEN_ENV = "POSTMARK_SERVER_TOKEN"
SERVER_ID_ENV = "POSTMARK_SERVER_ID"
SENDER_ENV = "POSTMARK_SENDER"
STREAM_ENV = "POSTMARK_MESSAGE_STREAM"
INBOUND_DOMAIN_ENV = "POSTMARK_INBOUND_DOMAIN"
CREDENTIAL_REF_ENV = "POSTMARK_CREDENTIAL_REF"
RECIPIENT_ENV = "POSTMARK_SMOKE_RECIPIENT"     # owner-controlled recipient (owner declaration)
ACCEPTED_SHA_ENV = "POSTMARK_SMOKE_ACCEPTED_SHA"

SMOKE_SPEC = {
    "channel": CHANNEL,
    "message_stream": MESSAGE_STREAM,
    "subject": SMOKE_SUBJECT,
    "body_hash": canonical_payload_hash({"body": SMOKE_BODY}),
    "spend_ceiling_usd": str(CEILING),
    "max_sends": MAX_SENDS,
    "max_attempts": MAX_ATTEMPTS,
    "cc_bcc_attachments": False,
    "recipient_owner_declared": True,
    "lifecycle_change": "none",
    "claims": "send_boundary_only__no_inbox_reply_demand_or_commercial_validation",
}


class PostmarkSmokeSpecMismatch(Exception):
    """The frozen Postmark smoke spec was tampered with — abort BEFORE any send."""


def compute_smoke_spec_hash(spec: dict) -> str:
    return canonical_payload_hash(spec)


FROZEN_POSTMARK_SMOKE_SPEC_HASH = "155987fee4985b1e1fcc7042f2c3244f42294fda6ba7095afcc48210700cb83f"


def assert_frozen() -> str:
    actual = compute_smoke_spec_hash(SMOKE_SPEC)
    if actual != FROZEN_POSTMARK_SMOKE_SPEC_HASH:
        raise PostmarkSmokeSpecMismatch(
            f"postmark smoke spec hash {actual} != frozen {FROZEN_POSTMARK_SMOKE_SPEC_HASH}")
    return actual
