"""Gate 8 / Slice 5 — the real-provider OPERATIONAL boundary (no customer send).

Two small, provider-specific pieces sitting AROUND the frozen Slice-4 machinery — they add no new
runtime, platform, or provider abstraction and weaken none of the frozen invariants:

1. ``check_provider_identity`` — a NON-CONSEQUENTIAL identity check (GET /server only) proving the
   runtime credential belongs to the expected actual Postmark Server ID, that DeliveryType is Live,
   and that the configured MessageStream matches. It NEVER sends an email and never returns the token.

2. ``handle_webhook`` — the smallest APPLICATION-LAYER ingress guard in front of the frozen
   provenance ingestion. It enforces POST-only, an optional (deployment-supplied) source-IP
   allowlist, JSON content-type, a bounded body, Basic-Auth, and strict JSON BEFORE any event reaches
   the hardened reconciler, and it writes NO canonical lifecycle itself: Delivery/Bounce ->
   ``ingest_postmark_event``, Inbound -> ``ingest_postmark_reply``, each of which independently
   reconciles the event to a VERIFIED outbound proof.

Security posture (source-confirmed against Postmark docs): Postmark does NOT sign webhooks (no HMAC)
and publishes only four webhook source IPs that it explicitly does NOT guarantee are stable (IP
allow-listing is being deprecated). So an authoritative IP allowlist is NOT relied upon here; the
primary controls are HTTPS (network edge) + Basic-Auth + strict method/content/size/JSON validation +
exact provider reconciliation. ``allowed_ips`` is accepted for optional defense-in-depth only, and no
IP range is hard-coded. HTTPS and POST-only at the network edge remain the DEPLOYMENT layer's job.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from ..errors import ConfigError, MarketAuthorityError
from . import postmark as pm

# A Postmark webhook body is small JSON; bound it well below any reasonable event to reject abuse.
MAX_WEBHOOK_BODY_BYTES = 262_144   # 256 KiB
_ALLOWED_RECORD_TYPES = frozenset({"Delivery", "Bounce", "Inbound"})


def check_provider_identity(transport, source, *, expected_server_id, expected_message_stream) -> dict:
    """NON-CONSEQUENTIAL real-provider identity check. Reads GET /server via the transport and proves
    the runtime credential belongs to the expected actual Server ID AND that DeliveryType is Live; the
    configured MessageStream is checked against the expectation. Sends NOTHING. The opaque
    credential_ref is reported; the raw token is never read into the result."""
    st = transport.get_server_state()
    checks = {
        "SERVER_IDENTITY": str(st.server_id) == str(expected_server_id),
        "LIVE_PROVIDER": str(st.delivery_type) == pm.REQUIRED_DELIVERY_TYPE,
        "MESSAGE_STREAM": str(source.message_stream) == str(expected_message_stream),
    }
    return {
        "server_id": str(st.server_id),
        "delivery_type": str(st.delivery_type),
        "message_stream": str(source.message_stream),
        "credential_ref": str(source.credential_ref),   # opaque OS-secret handle only, never the token
        "checks": checks,
        "ready": all(checks.values()),
    }


class WebhookRejected(Exception):
    """An ingress-layer rejection carrying the HTTP status the edge should return. Deterministic; NO
    canonical state is written on rejection, and the raw body is never echoed."""

    def __init__(self, http_status: int, reason: str):
        super().__init__(f"{http_status} {reason}")
        self.http_status = http_status
        self.reason = reason


@dataclass(frozen=True)
class IngressResult:
    http_status: int
    record_type: str
    market_observation_id: Optional[str] = None


def _headers_lower(headers) -> dict:
    return {str(k).lower(): v for k, v in dict(headers or {}).items()}


def handle_webhook(conn, *, method, headers, body, source, transport, actor: str = "market",
                   remote_ip=None, allowed_ips=None) -> IngressResult:
    """Validate a raw Postmark webhook request and, only if it survives every application-layer
    control, route it to the frozen provenance ingestion. Rejections raise ``WebhookRejected`` with an
    HTTP status and never touch canonical state. The raw body is never logged."""
    if str(method).upper() != "POST":
        raise WebhookRejected(405, "method not allowed")
    # Optional source-IP allowlist — enforced ONLY when the deployment supplies an authoritative list.
    # Postmark does not guarantee stable webhook IPs, so this is defense-in-depth, never the primary
    # control, and no range is hard-coded here.
    if allowed_ips is not None and str(remote_ip) not in {str(ip) for ip in allowed_ips}:
        raise WebhookRejected(403, "source IP not allowlisted")
    h = _headers_lower(headers)
    if "application/json" not in str(h.get("content-type", "")).lower():
        raise WebhookRejected(415, "unsupported media type")
    raw = bytes(body) if isinstance(body, (bytes, bytearray)) else str(body or "").encode("utf-8")
    if len(raw) > MAX_WEBHOOK_BODY_BYTES:
        raise WebhookRejected(413, "payload too large")
    # Authenticate BEFORE parsing the (still untrusted) body, so an unauthenticated request never
    # reaches JSON handling or the reconciler.
    auth_header = str(h.get("authorization", ""))
    try:
        pm._authenticate(source, auth_header)
    except pm.WebhookAuthError as exc:
        raise WebhookRejected(401, "unauthorized") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise WebhookRejected(400, "malformed JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookRejected(400, "JSON object required")
    record_type = str(payload.get("RecordType"))
    if record_type not in _ALLOWED_RECORD_TYPES:
        raise WebhookRejected(422, "unsupported RecordType")
    try:
        if record_type == "Inbound":
            res = pm.ingest_postmark_reply(conn, payload, source=source, auth_header=auth_header,
                                           transport=transport, actor=actor)
        else:
            res = pm.ingest_postmark_event(conn, payload, source=source, auth_header=auth_header,
                                           transport=transport, actor=actor)
    except pm.WebhookAuthError as exc:                 # re-auth inside ingestion (kept as one authority)
        raise WebhookRejected(401, "unauthorized") from exc
    except MarketAuthorityError as exc:                # authenticated, but provenance/reconciliation failed
        raise WebhookRejected(422, "unprocessable") from exc
    return IngressResult(202, record_type, getattr(res, "market_observation_id", None))


# ==========================================================================
# operational entrypoint: runtime env -> real GET /server identity check (no send)
# ==========================================================================
# Non-secret expected config (owner-recorded) + the ONE secret, all from the runtime environment.
ENV_SERVER_TOKEN = "POSTMARK_SERVER_TOKEN"          # SECRET — the server API token (never logged/persisted)
ENV_SERVER_ID = "POSTMARK_SERVER_ID"                # non-secret — expected actual Postmark Server ID
ENV_MESSAGE_STREAM = "POSTMARK_MESSAGE_STREAM"      # non-secret — configured/expected MessageStream
ENV_SENDER = "POSTMARK_SENDER"                      # non-secret — intended From (optional here)
ENV_INBOUND_DOMAIN = "POSTMARK_INBOUND_DOMAIN"      # non-secret — inbound Reply-To domain (optional here)
ENV_SUBJECT = "POSTMARK_SUBJECT"                    # non-secret — default subject (optional here)
ENV_CREDENTIAL_REF = "POSTMARK_CREDENTIAL_REF"      # non-secret — opaque canonical handle (not the token)


def _source_from_env(env: dict, *, server_id: str, message_stream: str) -> pm.PostmarkSource:
    return pm.PostmarkSource(
        postmark_server_id=server_id, message_stream=message_stream,
        sender=env.get(ENV_SENDER, ""), default_subject=env.get(ENV_SUBJECT, ""),
        inbound_domain=env.get(ENV_INBOUND_DOMAIN, ""),
        credential_ref=env.get(ENV_CREDENTIAL_REF, "secret://postmark/alpha"))


def resolve_identity_from_env(env=None, *, transport_factory=None, source=None) -> dict:
    """Read the approved runtime environment, construct the real ``PostmarkHttpTransport`` from the
    token, and run the NON-CONSEQUENTIAL identity check (GET /server only). Fails CLOSED: a missing
    token or missing expected Server ID raises ``ConfigError`` (non-secret message); a provider/network
    fault returns a non-ready result classified by exception TYPE only. The raw token is passed to the
    transport factory and is NEVER placed in the result, an exception message, or a log. Sends nothing.

    ``transport_factory``/``source`` are test seams (default: the real transport and an env-built
    source); tests inject a stub so no real network or token is required.
    """
    env = os.environ if env is None else env
    token = env.get(ENV_SERVER_TOKEN)
    if not token:
        raise ConfigError(f"{ENV_SERVER_TOKEN} is not set")   # fail closed; no token to leak
    expected_server_id = env.get(ENV_SERVER_ID)
    if not expected_server_id:
        raise ConfigError(f"{ENV_SERVER_ID} is not set")
    expected_message_stream = env.get(ENV_MESSAGE_STREAM) or "outbound"
    if source is None:
        source = _source_from_env(env, server_id=expected_server_id, message_stream=expected_message_stream)
    factory = transport_factory or pm.PostmarkHttpTransport
    transport = factory(token)                                # token used ONLY to build the transport
    try:
        result = check_provider_identity(transport, source, expected_server_id=expected_server_id,
                                         expected_message_stream=expected_message_stream)
    except Exception as exc:                                  # network/provider fault -> fail closed
        # classify by exception TYPE only; never echo the message (defense against token/URL leakage)
        return {"ready": False, "reason": f"provider_error:{type(exc).__name__}",
                "expected_server_id": str(expected_server_id),
                "expected_message_stream": str(expected_message_stream)}
    result["expected_server_id"] = str(expected_server_id)
    result["expected_message_stream"] = str(expected_message_stream)
    return result


def main(argv=None, *, env=None, transport_factory=None, out=None) -> int:
    """CLI shim: print the operational readiness result as JSON (NO secret) and return an exit code —
    0 ready, 1 not ready, 2 configuration error. Not a market Proof Receipt; operational evidence only."""
    import sys
    out = sys.stdout if out is None else out
    try:
        result = resolve_identity_from_env(env=env, transport_factory=transport_factory)
    except ConfigError as exc:
        print(json.dumps({"ready": False, "error": f"config:{type(exc).__name__}", "detail": str(exc)}), file=out)
        return 2
    print(json.dumps(result, sort_keys=True), file=out)
    return 0 if result.get("ready") else 1


if __name__ == "__main__":   # pragma: no cover - manual operational invocation
    raise SystemExit(main())
