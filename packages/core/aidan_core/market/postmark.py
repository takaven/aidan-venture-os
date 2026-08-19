"""Postmark email — ONE replaceable real-channel adapter for the Gate-8 Alpha (Slice 2).

Postmark is an adapter at the edge, never architecture: it is an ordinary Gate-4
``WorkerAdapter`` plus a deterministic MARKET_ACTION verifier and a small provider-event
normalizer that feeds the EXISTING ``market_observation`` system. Nothing here defines system
semantics; the provider name appears only in this module and its configuration.

Load-bearing boundaries (unchanged from Gate 7):
  * a worker's claim is inert — canonical MARKET_ACTION proof requires INDEPENDENT provider
    state. The verifier queries provider state BY opaque correlation metadata (venture +
    market_action_spec + action_request), so a worker that lies about its MessageID, sends
    nothing, or tampers body/recipient/sender/source is caught.
  * action proof (the exact authorized email was accepted by Postmark) is NOT a market
    outcome. Delivery/Bounce/inbound-Reply are later, separate ``market_observation`` evidence.

Authenticity: Postmark does NOT sign webhooks (no HMAC). Its official boundary is HTTP Basic
Auth on the webhook URL + IP allowlisting. We therefore authenticate events by a shared
Basic-Auth secret AND reconcile every consequential event against provider state / canonical
correlation before it becomes evidence — never trusting an unauthenticated raw POST. This
limitation is explicit (see ADR-027).

Slice 2 performs NO real send, uses NO live credential, and makes NO network call: a
deterministic in-memory ``PostmarkTransport`` fake holds provider-side state in tests. The
real ``PostmarkHttpTransport`` is defined but never exercised in the suite; live credentials,
sender identity, recipient authorization, and network execution are Slice-5 dependencies.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from psycopg.types.json import Json

from .. import audit, db
from ..errors import MarketAuthorityError, NotFoundError
from ..factory import runtime as factory_runtime
from ..factory import spec as spec_mod
from ..factory.verifiers import VerificationRequest, VerificationResult, VerifierRegistry
from ..factory.workers import WorkerRegistry, WorkerResult
from . import action as action_mod
from . import channels as channels_mod
from . import origin as origin_mod
from .observation import record_market_observation

POSTMARK_CHANNEL = "postmark-email"
POSTMARK_VERIFIER_KIND = "postmark-email"
MARKET_ACTION = "MARKET_ACTION"

# A real Postmark submission is not proven exactly-once by the API result alone; an ambiguous
# result is reconciled against provider state via the opaque correlation metadata (below)
# before any retry. Never blind-retry an ambiguous real send.
POSTMARK_SEND_SAFETY = "RECONCILABLE"

# Verified provider events normalize ONLY into the existing finite observation vocabulary.
_EVENT_TO_OBSERVATION = {"Delivery": "DELIVERED", "Bounce": "BOUNCED"}


@dataclass(frozen=True)
class PostmarkSource:
    """Trusted, venture-scoped provider configuration. Carries an OPAQUE credential reference
    (never a raw token in canonical state) and the source-authorized sender/subject/streams.
    The webhook Basic-Auth secret authenticates inbound provider callbacks."""

    server_id: str
    sender: str                    # source-authorized From; the worker cannot choose another
    default_subject: str           # source-authorized subject; not worker prose
    inbound_domain: str            # Reply-To domain for inbound correlation
    credential_ref: str            # opaque lookup handle; the real token lives outside canon
    webhook_user: str = ""
    webhook_secret: str = ""


def correlation_metadata(venture_id, market_action_spec_id, action_request_id, action_spec_hash) -> dict:
    """Opaque canonical correlation carried as Postmark Metadata — no PII, no secret."""
    return {
        "venture": str(venture_id),
        "market_action_spec": str(market_action_spec_id),
        "action_request": str(action_request_id),
        "action_spec_hash": str(action_spec_hash),
    }


def reply_mailbox_hash(venture_id, market_action_spec_id) -> str:
    """Deterministic, opaque inbound-reply correlation token (Postmark MailboxHash). Contains
    no PII/secret and is structural: an inbound reply resolves to exactly one market action."""
    return hashlib.sha256(f"{venture_id}:{market_action_spec_id}".encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------
# provider transport boundary (tiny + Postmark-specific)
# --------------------------------------------------------------------------
@runtime_checkable
class PostmarkTransport(Protocol):
    def send_email(self, *, server_id: str, sender: str, to: str, subject: str, text_body: str,
                   reply_to: str, metadata: dict) -> str: ...
    def get_outbound_message(self, message_id: str) -> Optional[dict]: ...
    def find_outbound_by_correlation(self, correlation: dict) -> list[dict]: ...
    def check_webhook_auth(self, auth_header: str) -> bool: ...


@runtime_checkable
class RecipientResolver(Protocol):
    """Venture/source-scoped audience_ref -> exact destination address. The worker cannot
    substitute an arbitrary recipient; the verifier resolves the same mapping independently."""

    def resolve(self, venture_id: str, source_instance_ref: str, audience_ref: str) -> str: ...


# --------------------------------------------------------------------------
# outbound worker (a plain Gate-4 WorkerAdapter)
# --------------------------------------------------------------------------
class PostmarkEmailWorker:
    """Sends the EXACT frozen market action through Postmark and returns the MessageID as a
    CLAIM only. ``mode`` lets tests choose observable provider behaviour (compliant / adversarial);
    the worker never gains authority — only the independent verifier decides."""

    kind = POSTMARK_VERIFIER_KIND

    def __init__(self, transport: PostmarkTransport, resolver: RecipientResolver,
                 source: PostmarkSource, *, mode: str = "compliant"):
        self._t = transport
        self._resolver = resolver
        self._source = source
        self.mode = mode
        self.calls = 0
        self.last_request = None

    def execute(self, request) -> WorkerResult:  # no DB access, no canonical authority
        self.calls += 1
        self.last_request = request
        m = dict((request.task_payload or {}).get("market", {}))
        pm = dict((request.task_payload or {}).get("postmark", {}))
        source = self._source
        server_id, sender = source.server_id, source.sender
        recipient = self._resolver.resolve(str(request.venture_id), m["source_instance_ref"], m["audience_ref"])
        text_body = m["content"]
        if self.mode == "wrong_content":
            text_body = text_body + " TAMPERED"
        if self.mode == "wrong_audience":
            recipient = "attacker@elsewhere.test"
        if self.mode == "wrong_sender":
            sender = "spoofed@elsewhere.test"
        if self.mode == "wrong_source":
            server_id = "server-OTHER"
        message_id = "claimed-nonexistent"
        if self.mode != "nothing":
            message_id = self._t.send_email(
                server_id=server_id, sender=sender, to=recipient, subject=source.default_subject,
                text_body=text_body, reply_to=pm["reply_to"], metadata=dict(pm["correlation"]))
        return WorkerResult(
            worker_kind=self.kind, external_result_id=message_id, reported_outcome="success",
            worker_version="postmark-test",
            structured_output={"message_id": message_id, "sent": True, "delivered": True})


# --------------------------------------------------------------------------
# independent MARKET_ACTION verifier (reads provider state, not the worker claim)
# --------------------------------------------------------------------------
class PostmarkActionVerifier:
    """VERIFIED iff Postmark's own state holds the exact authorized email for this action.

    Deterministic, no score, no DB. It is constructed with the provider transport + recipient
    resolver + source config and queries provider state BY the opaque canonical correlation —
    independent of the worker's structured output. Plain-text body identity only (Postmark
    preserves TextBody exactly); HTML/MIME identity is intentionally out of scope for Alpha.
    """

    kind = POSTMARK_VERIFIER_KIND
    verification_type = MARKET_ACTION

    def __init__(self, transport: PostmarkTransport, resolver: RecipientResolver, source: PostmarkSource):
        self._t = transport
        self._resolver = resolver
        self._source = source

    def verify(self, request: VerificationRequest) -> VerificationResult:
        market = dict((request.expected_output_contract or {}).get("market", {}))
        pm = dict((request.expected_output_contract or {}).get("postmark", {}))
        correlation = dict(pm.get("correlation", {}))
        claimed = str((request.worker_structured_output or {}).get("message_id"))

        found = self._t.find_outbound_by_correlation(correlation) if correlation else []
        exists = len(found) >= 1
        msg = found[0] if exists else {}
        expected_recipient = None
        if correlation:
            expected_recipient = self._resolver.resolve(
                correlation.get("venture"), market.get("source_instance_ref"), market.get("audience_ref"))

        checks = []
        checks.append(("ACTION_EXISTS", exists))
        if exists:
            checks.append(("ACTION_IDENTITY",
                           channels_mod.content_sha(str(msg.get("TextBody", ""))) == market.get("content_hash")
                           and str(msg.get("Metadata", {}).get("action_spec_hash")) == str(market.get("action_spec_hash"))))
            checks.append(("AUDIENCE_IDENTITY", str(msg.get("To")) == str(expected_recipient)))
            checks.append(("CHANNEL_IDENTITY", str(msg.get("ServerID")) == str(self._source.server_id)))
            checks.append(("SENDER_IDENTITY", str(msg.get("From")) == str(self._source.sender)))
            checks.append(("ACCEPTANCE_IDENTITY", claimed == str(msg.get("MessageID")) and claimed not in ("", "None")))
        else:
            for name in ("ACTION_IDENTITY", "AUDIENCE_IDENTITY", "CHANNEL_IDENTITY", "SENDER_IDENTITY", "ACCEPTANCE_IDENTITY"):
                checks.append((name, False))

        ok = all(v for _n, v in checks)
        from ..actions import canonical_payload_hash
        evidence = {
            "market_action_spec_id": market.get("market_action_spec_id"),
            "action_spec_hash": market.get("action_spec_hash"),
            "message_id": msg.get("MessageID") if exists else None,
            "checks": [[n, bool(v)] for n, v in checks],
        }
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            canonical_payload_hash({"attempt": str(request.execution_attempt_id), **evidence}),
            detail={"checks": [{"name": n, "result": "PASS" if v else "FAIL"} for n, v in checks]})


def postmark_verifier_registry(transport: PostmarkTransport, resolver: RecipientResolver,
                               source: PostmarkSource) -> VerifierRegistry:
    reg = VerifierRegistry()
    reg.register(PostmarkActionVerifier(transport, resolver, source))
    return reg


# --------------------------------------------------------------------------
# prepare / execute / verify (reuse the Gate-7 market binding + Gate-4 runtime)
# --------------------------------------------------------------------------
def _postmark_contract(conn, action_request_id, source: PostmarkSource):
    ms_row = action_mod.get_market_action_spec(conn, action_request_id)
    if ms_row is None:
        raise MarketAuthorityError(
            f"no frozen market_action_spec for action {action_request_id}; "
            "a Postmark market execution cannot be prepared from free-form intent")
    (_id, venture_id, _arid, _opp, _vt, channel_kind, audience_ref, _prov, content, content_hash,
     _offer, _pa, _pc, _terms, _spend, _cur, action_spec_hash, _created) = ms_row
    if channel_kind != POSTMARK_CHANNEL:
        raise MarketAuthorityError(
            f"market action channel is {channel_kind!r}, not {POSTMARK_CHANNEL!r}")
    source_instance = channels_mod.source_instance_ref(venture_id, channel_kind)
    correlation = correlation_metadata(venture_id, _id, action_request_id, action_spec_hash)
    reply_to = f"reply+{reply_mailbox_hash(venture_id, _id)}@{source.inbound_domain}"
    market = {
        "market_action_spec_id": str(_id), "action_spec_hash": action_spec_hash,
        "channel_kind": channel_kind, "audience_ref": audience_ref, "content": content,
        "content_hash": content_hash, "source_instance_ref": source_instance,
    }
    postmark = {"correlation": correlation, "reply_to": reply_to, "server_id": source.server_id}
    task_payload = {"market": market, "postmark": postmark}
    contract = {"market": market, "postmark": {"correlation": correlation}}
    return task_payload, contract


def prepare_postmark_execution(conn, action_request_id: str, *, source: PostmarkSource,
                               timeout_seconds: int = 60, max_attempts: int = 1, actor: str = "market"):
    """Bind the frozen market_action_spec into an execution spec forcing the Postmark verifier."""
    task_payload, contract = _postmark_contract(conn, action_request_id, source)
    spec = spec_mod.create_execution_spec(
        conn, action_request_id, worker_kind=POSTMARK_VERIFIER_KIND, verifier_kind=POSTMARK_VERIFIER_KIND,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=["SEND_OUTREACH"], task_payload=task_payload,
        expected_output_contract=contract, actor=actor)
    return spec


def execute_postmark_action(conn, action_request_id: str, *, registry: WorkerRegistry,
                            source: PostmarkSource, max_attempts: int = 1, actor: str = "market", **kw):
    prepare_postmark_execution(conn, action_request_id, source=source, max_attempts=max_attempts, actor=actor)
    return factory_runtime.execute_action(
        conn, action_request_id, registry=registry, workspace_ref=f"market://{POSTMARK_CHANNEL}", actor=actor, **kw)


def verify_postmark_action(conn, action_request_id: str, *, transport: PostmarkTransport,
                           resolver: RecipientResolver, source: PostmarkSource, actual_cost=0, actor: str = "market"):
    """Deterministically verify the exact email occurred in Postmark and, only if VERIFIED,
    complete via the canonical proof-gated path (the ONE MARKET_ACTION proof_receipt) AND bind a
    durable evidence origin taken from the transport's OWN declared origin (never a caller flag)."""
    outcome = factory_runtime.verify_and_complete(
        conn, action_request_id, verifier_registry=postmark_verifier_registry(transport, resolver, source),
        actual_cost=actual_cost, actor=actor)
    if getattr(outcome, "verified", False):
        with conn.cursor() as cur:
            cur.execute("SELECT venture_id FROM action_request WHERE id = %s", (action_request_id,))
            venture_id = cur.fetchone()[0]
        origin_mod.record_evidence_origin(
            conn, action_request_id,
            origin_kind=getattr(transport, "origin_kind", origin_mod.SIMULATED),
            provider_kind="postmark",
            source_instance_ref=channels_mod.source_instance_ref(venture_id, POSTMARK_CHANNEL), actor=actor)
    return outcome


# --------------------------------------------------------------------------
# provider-event normalization -> existing market_observation
# --------------------------------------------------------------------------
def _authenticate(source: PostmarkSource, auth_header: str) -> None:
    expected = "Basic " + base64.b64encode(f"{source.webhook_user}:{source.webhook_secret}".encode()).decode()
    if not auth_header or auth_header != expected:
        raise MarketAuthorityError("Postmark webhook authentication failed (Basic-Auth mismatch)")


def _spec_for_message(conn, message_id: str, transport: PostmarkTransport):
    """Independent reconciliation: resolve a provider MessageID to its canonical market action
    via provider Metadata (never trusting event fields alone)."""
    msg = transport.get_outbound_message(message_id)
    if msg is None:
        raise MarketAuthorityError(f"Postmark MessageID {message_id!r} has no reconcilable outbound record")
    spec_id = str(msg.get("Metadata", {}).get("market_action_spec"))
    with conn.cursor() as cur:
        cur.execute("SELECT id, venture_id FROM market_action_spec WHERE id = %s AND channel_kind = %s",
                    (spec_id, POSTMARK_CHANNEL))
        row = cur.fetchone()
    if row is None:
        raise MarketAuthorityError("provider message does not reconcile to a canonical Postmark market action")
    return str(row[0]), str(row[1])


def ingest_postmark_event(conn, raw_event: dict, *, source: PostmarkSource, auth_header: str,
                          transport: PostmarkTransport, actor: str = "market"):
    """Authenticate a Postmark Delivery/Bounce webhook, reconcile its MessageID to the exact
    canonical market action, and append the existing source-scoped market_observation."""
    _authenticate(source, auth_header)
    record_type = str(raw_event.get("RecordType"))
    otype = _EVENT_TO_OBSERVATION.get(record_type)
    if otype is None:
        raise MarketAuthorityError(f"unsupported Postmark event RecordType {record_type!r}")
    message_id = str(raw_event.get("MessageID"))
    spec_id, _venture = _spec_for_message(conn, message_id, transport)
    external_event_id = f"{record_type}:{message_id}"
    return record_market_observation(
        conn, spec_id, external_event_id=external_event_id, observation_type=otype,
        channel_kind=POSTMARK_CHANNEL, raw_evidence=dict(raw_event), actor=actor)


def ingest_postmark_reply(conn, raw_inbound: dict, *, source: PostmarkSource, auth_header: str,
                          actor: str = "market"):
    """Authenticate a Postmark Inbound reply and correlate it to exactly one market action by
    the structural MailboxHash token (never by subject/body similarity). Reply text stays DATA."""
    _authenticate(source, auth_header)
    mailbox_hash = str(raw_inbound.get("MailboxHash"))
    if not mailbox_hash or mailbox_hash == "None":
        raise MarketAuthorityError("inbound reply carries no MailboxHash correlation token")
    with conn.cursor() as cur:
        cur.execute("SELECT id, venture_id FROM market_action_spec WHERE channel_kind = %s", (POSTMARK_CHANNEL,))
        rows = cur.fetchall()
    match = [(str(sid), str(vid)) for sid, vid in rows if reply_mailbox_hash(vid, sid) == mailbox_hash]
    if len(match) != 1:
        raise MarketAuthorityError("inbound reply MailboxHash does not correlate to exactly one market action")
    spec_id, _venture = match[0]
    external_event_id = f"Inbound:{raw_inbound.get('MessageID')}"
    return record_market_observation(
        conn, spec_id, external_event_id=external_event_id, observation_type="REPLIED",
        channel_kind=POSTMARK_CHANNEL, raw_evidence=dict(raw_inbound), actor=actor)


# --------------------------------------------------------------------------
# production HTTP transport (stdlib only; NOT exercised in the Slice-2 suite)
# --------------------------------------------------------------------------
class PostmarkHttpTransport:
    """Real Postmark transport over stdlib urllib (no third-party dependency). Never invoked by
    the test suite — live token/network/recipient are Slice-5 operational dependencies. The
    server token is supplied at construction from a trusted runtime source, never from canonical
    action state."""

    origin_kind = origin_mod.REAL_PROVIDER   # a real provider-backed evidence path
    _API = "https://api.postmarkapp.com"

    def __init__(self, server_token: str):
        self._token = server_token  # trusted runtime injection; never persisted in canon

    def _headers(self) -> dict:
        return {"X-Postmark-Server-Token": self._token, "Accept": "application/json",
                "Content-Type": "application/json"}

    def send_email(self, *, server_id, sender, to, subject, text_body, reply_to, metadata) -> str:  # pragma: no cover
        import json
        import urllib.request
        body = json.dumps({"From": sender, "To": to, "Subject": subject, "TextBody": text_body,
                           "ReplyTo": reply_to, "Metadata": metadata, "MessageStream": server_id}).encode()
        req = urllib.request.Request(f"{self._API}/email", data=body, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req) as resp:  # network — Slice-5 only
            return json.loads(resp.read()).get("MessageID")

    def get_outbound_message(self, message_id):  # pragma: no cover
        import json
        import urllib.request
        req = urllib.request.Request(f"{self._API}/messages/outbound/{message_id}/details",
                                     headers=self._headers(), method="GET")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def find_outbound_by_correlation(self, correlation):  # pragma: no cover
        raise NotImplementedError("Slice-5 operational dependency: live Metadata search")

    def check_webhook_auth(self, auth_header):  # pragma: no cover
        raise NotImplementedError("Slice-5 operational dependency: live webhook Basic-Auth")
