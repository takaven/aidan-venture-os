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

Authenticity: Postmark does NOT sign webhooks (no HMAC). THIS code authenticates events by a
shared Basic-Auth secret AND reconciles every consequential event against provider state /
canonical correlation before it becomes evidence — never trusting an unauthenticated raw POST.
Postmark also recommends IP allowlisting, but that is an INGRESS/firewall boundary and is NOT
enforced by this code; it is a Slice-5 operational-evidence requirement (HTTPS + Basic-Auth +
Postmark IP allowlist + POST-only + payload validation), not something these comments prove.

No real send, no live credential, no network call in tests: a deterministic in-memory
``PostmarkTransport`` fake holds provider-side state, and the genuine ``PostmarkHttpTransport``
is exercised by stubbing its single low-level HTTP boundary (``_http_request``) — never by
subclassing/replacing it. Live credentials, sender identity, recipient authorization, and real
network execution remain Slice-5 dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from psycopg.types.json import Json

from .. import audit, db, execution
from ..errors import AmbiguousExternalEffectError, MarketAuthorityError, NotFoundError
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
# Every consequential-path Postmark HTTP call is bounded by this explicit timeout (no unbounded call).
POSTMARK_HTTP_TIMEOUT_SECONDS = 30


class PostmarkSendRejected(MarketAuthorityError):
    """A Postmark POST /email returned a 4xx client rejection: the send was deterministically NOT
    accepted, so it is a PROVEN no-external-effect failure (ordinarily retryable) — distinct from an
    ambiguous no-response / 5xx fault where the effect may have occurred."""


class PostmarkReconcileUnknown(AmbiguousExternalEffectError):
    """A Postmark read-back/reconciliation could NOT be determined — HTTP 5xx, a timeout/network
    fault, or a malformed/unexpected payload. It is NEVER an empty 'no prior send' result. Being an
    AmbiguousExternalEffectError, it fails the action CLOSED into RECOVERY_REQUIRED (not auto-claimable,
    never blind-retried) on the send path, so provider uncertainty can never trigger a duplicate send;
    the verifier catches it and REJECTS (unknown provider state is never VERIFIED)."""

# Verified provider events normalize ONLY into the existing finite observation vocabulary.
_EVENT_TO_OBSERVATION = {"Delivery": "DELIVERED", "Bounce": "BOUNCED"}


@dataclass(frozen=True)
class PostmarkSource:
    """Trusted, venture-scoped provider configuration — NON-SECRET identity only. The actual
    Postmark SERVER ID (from GET /server) and the MessageStream id are distinct Postmark concepts;
    the opaque credential_ref is an OS secret HANDLE and is NOT proof that the runtime token
    belongs to that server (the transport proves that independently via GET /server). The raw
    server token and webhook secret never enter canonical state."""

    postmark_server_id: str        # actual Postmark Server ID (GET /server) — non-secret
    message_stream: str            # Postmark MessageStream id (e.g. 'outbound')
    sender: str                    # source-authorized From; the worker cannot choose another
    default_subject: str           # source-authorized subject; not worker prose
    inbound_domain: str            # Reply-To domain for inbound correlation
    credential_ref: str            # opaque OS secret handle; the real token lives outside canon
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
REQUIRED_DELIVERY_TYPE = "Live"   # a REAL Postmark market action must run on a Live server


@dataclass(frozen=True)
class PostmarkServerState:
    """Trusted provider facts from the server-scoped GET /server (the SAME runtime token that
    sends): the actual Server ID AND its DeliveryType. DeliveryType is fixed at server creation and
    cannot change; a 'Sandbox' server ACCEPTS and 'delivers' messages (visible in the UI, webhooks,
    and API) WITHOUT ever reaching a real recipient — so a REAL market action requires
    ``delivery_type == 'Live'`` and a Sandbox server is blocked before any consequential send."""

    server_id: str
    delivery_type: str          # 'Live' | 'Sandbox'


@runtime_checkable
class PostmarkTransport(Protocol):
    def send_email(self, *, message_stream: str, sender: str, to: str, subject: str, text_body: str,
                   reply_to: str, metadata: dict) -> str: ...
    def get_outbound_message(self, message_id: str) -> Optional[dict]: ...
    def get_server_state(self) -> "PostmarkServerState": ...   # actual Server ID + DeliveryType
    def find_outbound_by_correlation(self, correlation: dict) -> list: ...   # reconcile ambiguous sends


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
        fs = dict(pm.get("source", {}))                       # FROZEN approved provider identity
        src = self._source
        # The runtime configuration is an I/O dependency, NOT authority: its NON-SECRET identity
        # must correspond to the frozen approved identity, else refuse to send (before any call).
        if (str(src.postmark_server_id) != str(fs.get("postmark_server_id"))
                or str(src.message_stream) != str(fs.get("message_stream"))
                or str(src.sender) != str(fs.get("sender"))
                or str(src.default_subject) != str(fs.get("subject"))
                or str(src.credential_ref) != str(fs.get("credential_ref"))):
            raise MarketAuthorityError("worker provider configuration does not match the frozen approved source")
        # PROVE the transport's actual runtime credential belongs to the frozen Postmark server AND
        # that the server is LIVE (GET /server) — a matching credential_ref string is NOT sufficient,
        # and a Sandbox server (which 'delivers' without reaching a real recipient) is refused BEFORE
        # any consequential send, never silently downgraded to SIMULATED after sending.
        server = self._t.get_server_state()
        if str(server.server_id) != str(fs.get("postmark_server_id")):
            raise MarketAuthorityError("transport credential does not belong to the frozen Postmark server")
        if str(server.delivery_type) != REQUIRED_DELIVERY_TYPE:
            raise MarketAuthorityError(
                "Postmark server is not Live; a Sandbox server cannot perform a real market action")
        recipient = self._resolver.resolve(str(request.venture_id), m["source_instance_ref"], m["audience_ref"])
        if _recipient_hash(recipient) != str(pm.get("recipient_hash")):
            raise MarketAuthorityError("resolved recipient does not match the frozen approved recipient")
        # send using the FROZEN identities (adversarial modes tamper the OUTBOUND value after the
        # authority check, to also exercise the independent verifier).
        message_stream, sender, subject, text_body = fs["message_stream"], fs["sender"], fs["subject"], m["content"]
        reply_to = pm["reply_to"]
        if self.mode == "wrong_content":
            text_body = text_body + " TAMPERED"
        if self.mode == "wrong_audience":
            recipient = "attacker@elsewhere.test"
        if self.mode == "wrong_sender":
            sender = "spoofed@elsewhere.test"
        if self.mode == "wrong_source":
            message_stream = "stream-OTHER"
        if self.mode == "wrong_subject":
            subject = "Unapproved subject line"
        if self.mode == "wrong_reply_to":
            reply_to = "reply+FORGED@reply.invalid"
        correlation = dict(pm["correlation"])
        if self.mode == "nothing":
            message_id = "claimed-nonexistent"                 # a proven NO-SEND (no external effect)
        else:
            # RECONCILABLE consequential send (ADR-034): never POST while provider state already holds
            # the exact correlated message, and on an AMBIGUOUS POST (it may have crossed the boundary)
            # reconcile against provider state before ANY retry — capture if present, else FAIL CLOSED.
            expected = {"correlation": correlation, "content_hash": m["content_hash"],
                        "recipient_hash": str(pm.get("recipient_hash")), "subject": fs["subject"],
                        "reply_to": pm["reply_to"], "sender": fs["sender"], "message_stream": fs["message_stream"]}
            message_id = self._send_reconcilably(
                expected, message_stream=message_stream, sender=sender, to=recipient, subject=subject,
                text_body=text_body, reply_to=reply_to, correlation=correlation)
        return WorkerResult(
            worker_kind=self.kind, external_result_id=message_id, reported_outcome="success",
            worker_version="postmark-test",
            structured_output={"message_id": message_id, "sent": True, "delivered": True})

    def _send_reconcilably(self, expected, *, message_stream, sender, to, subject, text_body,
                           reply_to, correlation) -> str:
        """Issue the consequential POST with an exactly-once guarantee. Provider state is the arbiter:
        a POST is never issued while the exact correlated message already exists, and a POST whose
        outcome is ambiguous (network fault after dispatch, or no usable MessageID) is reconciled
        against provider state — captured if the exact message landed, else failed closed with NO
        blind re-dispatch. Known PRE-send failures are handled earlier and never reach here."""
        existing = self._reconcile(expected, correlation)      # (1) never duplicate a prior send
        if existing is not None:
            return existing
        try:                                                   # (2) attempt the consequential POST
            mid = self._t.send_email(message_stream=message_stream, sender=sender, to=to, subject=subject,
                                     text_body=text_body, reply_to=reply_to, metadata=dict(correlation))
        except PostmarkSendRejected:
            # A received provider REJECTION (HTTP response, not accepted): the send was NOT accepted,
            # so this is a PROVEN no-effect failure — retryable (WORKER_ERROR), not recovery. Reconcile
            # defensively in case a compliant message somehow exists; otherwise it is a plain failure.
            reconciled = self._reconcile(expected, correlation)
            if reconciled is not None:
                return reconciled
            raise MarketAuthorityError("Postmark rejected the send (no external effect); retryable")
        except Exception:
            # AMBIGUOUS: no usable provider response — the POST MAY have crossed the boundary. Reconcile
            # against provider state; capture if the exact message landed, else FAIL CLOSED into recovery
            # (an empty, eventually-consistent search is NOT proof the effect did not occur).
            reconciled = self._reconcile(expected, correlation)
            if reconciled is not None:
                return reconciled                              # the send DID occur -> capture, never re-POST
            raise AmbiguousExternalEffectError(
                "ambiguous Postmark send could not be reconciled against provider state; failing closed")
        return str(mid)

    def _reconcile(self, expected, correlation):
        """The single provider MessageID for the exact frozen action iff provider state holds exactly
        one FULLY-compliant correlated message; None if none; if more than one, the effect is
        ambiguously duplicated -> fail CLOSED into recovery (never resolve to an arbitrary id)."""
        candidates = [msg for msg in self._t.find_outbound_by_correlation(dict(correlation))
                      if _message_matches_frozen(msg, expected)]
        if len(candidates) > 1:
            raise AmbiguousExternalEffectError(
                "multiple correlated Postmark messages exist for one action; failing closed (prior duplicate)")
        return str(candidates[0]["MessageID"]) if candidates else None


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

    def __init__(self, transport: PostmarkTransport):
        self._t = transport

    def verify(self, request: VerificationRequest) -> VerificationResult:
        # Fail CLOSED on undetermined provider state: an unknown read-back is NEVER VERIFIED.
        try:
            return self._verify(request)
        except PostmarkReconcileUnknown:
            from ..actions import canonical_payload_hash
            return VerificationResult(
                self.kind, "REJECTED", self.verification_type,
                canonical_payload_hash({"attempt": str(request.execution_attempt_id),
                                        "reason": "provider_state_unknown"}),
                detail={"reason": "provider_state_unknown"})

    def _verify(self, request: VerificationRequest) -> VerificationResult:
        market = dict((request.expected_output_contract or {}).get("market", {}))
        pm = dict((request.expected_output_contract or {}).get("postmark", {}))
        correlation = dict(pm.get("correlation", {}))
        # ALL authoritative expected values come from the IMMUTABLE contract — never a runtime
        # PostmarkSource/RecipientResolver supplied later.
        fs = dict(pm.get("source", {}))
        recipient_hash = str(pm.get("recipient_hash"))
        expected_subject = str(fs.get("subject"))
        expected_reply_to = _normalize_email(pm.get("reply_to"))
        # CANONICAL result identity: the provider object proven MUST be the exact captured
        # execution_result.external_result_id the Proof Receipt binds to. The worker's structured
        # message_id is inert unless it EQUALS that canonical id, and the provider lookup itself uses
        # the canonical id — so a worker can never verify object B while the proof is attached to
        # result A (external_result_id == structured message_id == provider MessageID, all three).
        canonical = str(request.external_result_id or "")
        claimed = str((request.worker_structured_output or {}).get("message_id"))
        result_ok = canonical not in ("", "None") and claimed == canonical

        # Independent provider fetch BY the canonical result id (works for the fake AND the real
        # PostmarkHttpTransport via GET /messages/outbound/{id}/details). The located message's own
        # Metadata is then checked against the canonical correlation.
        msg = self._t.get_outbound_message(canonical) if canonical not in ("", "None") else None
        exists = msg is not None
        msg = msg or {}
        meta = dict(msg.get("Metadata", {}))
        # the transport credential must belong to the frozen Postmark server AND be LIVE (GET /server)
        server = self._t.get_server_state()
        server_ok = str(server.server_id) == str(fs.get("postmark_server_id"))
        live_ok = str(server.delivery_type) == REQUIRED_DELIVERY_TYPE

        checks = []
        checks.append(("RESULT_IDENTITY", result_ok))
        checks.append(("ACTION_EXISTS", exists))
        checks.append(("SERVER_IDENTITY", server_ok))
        checks.append(("LIVE_PROVIDER", live_ok))
        if exists:
            checks.append(("CORRELATION_IDENTITY",
                           all(str(meta.get(k)) == str(correlation.get(k)) for k in correlation)))
            checks.append(("ACTION_IDENTITY",
                           channels_mod.content_sha(str(msg.get("TextBody", ""))) == market.get("content_hash")
                           and str(meta.get("action_spec_hash")) == str(market.get("action_spec_hash"))))
            checks.append(("SUBJECT_IDENTITY", str(msg.get("Subject")) == expected_subject))
            checks.append(("AUDIENCE_IDENTITY", _recipient_hash(str(msg.get("To"))) == recipient_hash))
            checks.append(("REPLY_TO_IDENTITY", _normalize_email(msg.get("ReplyTo")) == expected_reply_to))
            checks.append(("CHANNEL_IDENTITY", str(msg.get("MessageStream")) == str(fs.get("message_stream"))))
            checks.append(("SENDER_IDENTITY", str(msg.get("From")) == str(fs.get("sender"))))
            checks.append(("NOT_SANDBOXED", msg.get("Sandboxed") is not True))
            checks.append(("ACCEPTANCE_IDENTITY",
                           canonical == str(msg.get("MessageID")) and canonical not in ("", "None")))
        else:
            for name in ("CORRELATION_IDENTITY", "ACTION_IDENTITY", "SUBJECT_IDENTITY", "AUDIENCE_IDENTITY",
                         "REPLY_TO_IDENTITY", "CHANNEL_IDENTITY", "SENDER_IDENTITY", "NOT_SANDBOXED",
                         "ACCEPTANCE_IDENTITY"):
                checks.append((name, False))

        ok = all(v for _n, v in checks)
        from ..actions import canonical_payload_hash
        evidence = {
            "market_action_spec_id": market.get("market_action_spec_id"),
            "action_spec_hash": market.get("action_spec_hash"),
            "external_result_id": canonical,       # evidence binds the canonical captured result id
            "message_id": msg.get("MessageID") if exists else None,
            "checks": [[n, bool(v)] for n, v in checks],
        }
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            canonical_payload_hash({"attempt": str(request.execution_attempt_id), **evidence}),
            detail={"checks": [{"name": n, "result": "PASS" if v else "FAIL"} for n, v in checks]})


def postmark_verifier_registry(transport: PostmarkTransport) -> VerifierRegistry:
    reg = VerifierRegistry()
    reg.register(PostmarkActionVerifier(transport))
    return reg


def _frozen_contract_source(conn, action_request_id) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT expected_output_contract FROM execution_spec WHERE action_request_id = %s",
                    (action_request_id,))
        row = cur.fetchone()
    return dict((row[0] or {}).get("postmark", {}).get("source", {})) if row else {}


# --------------------------------------------------------------------------
# prepare / execute / verify (reuse the Gate-7 market binding + Gate-4 runtime)
# --------------------------------------------------------------------------
def source_identity(source: PostmarkSource) -> str:
    """Concrete NON-SECRET provider source identity (credential ref + actual server id) — no token."""
    return f"postmark:{source.credential_ref}:{source.postmark_server_id}"


def _recipient_hash(address: str) -> str:
    return hashlib.sha256(_normalize_email(address).encode("utf-8")).hexdigest()


def _message_matches_frozen(msg: dict, expected: dict) -> bool:
    """A provider message IS the exact frozen action only if its correlation, content, recipient,
    subject, Reply-To, sender and stream all match the frozen contract and it is not Sandboxed —
    a bare Metadata match is never sufficient for reconciliation (mirrors the verifier's identities)."""
    meta = dict(msg.get("Metadata", {}))
    return (all(str(meta.get(k)) == str(v) for k, v in expected["correlation"].items())
            and channels_mod.content_sha(str(msg.get("TextBody", ""))) == expected["content_hash"]
            and _recipient_hash(str(msg.get("To"))) == str(expected["recipient_hash"])
            and str(msg.get("Subject")) == str(expected["subject"])
            and _normalize_email(msg.get("ReplyTo")) == _normalize_email(expected["reply_to"])
            and str(msg.get("From")) == str(expected["sender"])
            and str(msg.get("MessageStream")) == str(expected["message_stream"])
            and msg.get("Sandboxed") is not True)


def _frozen_source(source: PostmarkSource) -> dict:
    """The exact non-secret provider execution identity frozen at authorization (never the token)."""
    return {"provider_kind": "postmark", "postmark_server_id": source.postmark_server_id,
            "message_stream": source.message_stream, "sender": source.sender,
            "subject": source.default_subject, "credential_ref": source.credential_ref,
            "inbound_domain": source.inbound_domain, "source_identity": source_identity(source)}


def _postmark_contract(conn, action_request_id, source: PostmarkSource, resolver: RecipientResolver):
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
    source_instance = channels_mod.source_instance_ref(venture_id, channel_kind)   # Gate-7 dedupe key
    correlation = correlation_metadata(venture_id, _id, action_request_id, action_spec_hash)
    reply_to = f"reply+{reply_mailbox_hash(venture_id, _id)}@{source.inbound_domain}"
    # Resolve + FREEZE the exact non-secret provider execution identities at authorization time, so
    # a later runtime source/resolver cannot substitute sender/source/recipient. Recipient is stored
    # as a deterministic hash (no plaintext PII in canonical state).
    frozen_source = _frozen_source(source)
    recipient_hash = _recipient_hash(resolver.resolve(str(venture_id), source_instance, audience_ref))
    market = {
        "market_action_spec_id": str(_id), "action_spec_hash": action_spec_hash,
        "channel_kind": channel_kind, "audience_ref": audience_ref, "content": content,
        "content_hash": content_hash, "source_instance_ref": source_instance,
    }
    frozen = {"correlation": correlation, "reply_to": reply_to,
              "source": frozen_source, "recipient_hash": recipient_hash}
    task_payload = {"market": market, "postmark": frozen}
    contract = {"market": market, "postmark": frozen}
    return task_payload, contract


def prepare_postmark_execution(conn, action_request_id: str, *, source: PostmarkSource,
                               resolver: RecipientResolver, timeout_seconds: int = 60,
                               max_attempts: int = 1, actor: str = "market"):
    """Freeze the exact approved provider source + recipient identity into the immutable execution
    contract and force the Postmark verifier. Runtime worker/verifier config cannot alter these."""
    task_payload, contract = _postmark_contract(conn, action_request_id, source, resolver)
    spec = spec_mod.create_execution_spec(
        conn, action_request_id, worker_kind=POSTMARK_VERIFIER_KIND, verifier_kind=POSTMARK_VERIFIER_KIND,
        timeout_seconds=timeout_seconds, max_attempts=max_attempts,
        capability_scope=["SEND_OUTREACH"], task_payload=task_payload,
        expected_output_contract=contract, actor=actor)
    return spec


def execute_postmark_action(conn, action_request_id: str, *, registry: WorkerRegistry,
                            source: PostmarkSource, resolver: RecipientResolver,
                            max_attempts: int = 1, actor: str = "market", **kw):
    prepare_postmark_execution(conn, action_request_id, source=source, resolver=resolver,
                               max_attempts=max_attempts, actor=actor)
    # Declare the ACTUAL send safety: a real email is a consequential, non-idempotent action, so it
    # must NOT inherit the factory IDEMPOTENT default. The worker enforces exactly-once against
    # provider state (never a blind duplicate); RECONCILABLE also governs crash/lease recovery.
    return factory_runtime.execute_action(
        conn, action_request_id, registry=registry, workspace_ref=f"market://{POSTMARK_CHANNEL}",
        safety_mode=POSTMARK_SEND_SAFETY, actor=actor, **kw)


def verify_postmark_action(conn, action_request_id: str, *, transport: PostmarkTransport,
                           actual_cost=0, actor: str = "market"):
    """Deterministically verify the exact email occurred in Postmark (all expected identities from
    the IMMUTABLE contract, not a runtime source/resolver) and, only if VERIFIED, complete via the
    canonical proof-gated path AND bind a durable evidence origin from the trusted reconciliation."""
    outcome = factory_runtime.verify_and_complete(
        conn, action_request_id, verifier_registry=postmark_verifier_registry(transport),
        actual_cost=actual_cost, actor=actor)
    if getattr(outcome, "verified", False):
        with conn.cursor() as cur:
            cur.execute("SELECT venture_id FROM action_request WHERE id = %s", (action_request_id,))
            venture_id = cur.fetchone()[0]
            cur.execute("SELECT er.external_result_id FROM execution_result er "
                        "JOIN proof_receipt pr ON pr.execution_result_id = er.id "
                        "WHERE pr.action_request_id = %s AND pr.verification_type = 'MARKET_ACTION' "
                        "AND pr.result = 'VERIFIED' ORDER BY pr.created_at DESC, pr.id DESC LIMIT 1",
                        (action_request_id,))
            row = cur.fetchone()
        message_id = row[0] if row else None
        fs = _frozen_contract_source(conn, action_request_id)      # concrete non-secret source identity
        # attest via the real reconciliation path (None for a fake/subclass transport -> SIMULATED);
        # the attested provider server must also match the frozen approved source, else not REAL.
        provider_state = _trusted_provider_state(transport, message_id) if message_id else None
        # the attested actual server AND message stream must match the frozen approved source
        if provider_state is not None and (
                str(getattr(provider_state, "server_id", "")) != str(fs.get("postmark_server_id"))
                or str(getattr(provider_state, "message_stream", "")) != str(fs.get("message_stream"))):
            provider_state = None
        origin_mod.record_evidence_origin(
            conn, action_request_id, provider_state=provider_state, provider_kind="postmark",
            source_instance_ref=str(fs.get("source_identity")), actor=actor)   # concrete frozen source id
    return outcome


def _frozen_expected(conn, action_request_id):
    """The frozen correlation + exact-identity expectations for reconciling a Postmark action."""
    with conn.cursor() as cur:
        cur.execute("SELECT expected_output_contract FROM execution_spec WHERE action_request_id = %s",
                    (action_request_id,))
        row = cur.fetchone()
    c = dict(row[0] or {}) if row else {}
    market = dict(c.get("market", {}))
    pm = dict(c.get("postmark", {}))
    fs = dict(pm.get("source", {}))
    correlation = dict(pm.get("correlation", {}))
    expected = {"correlation": correlation, "content_hash": market.get("content_hash"),
                "recipient_hash": str(pm.get("recipient_hash")), "subject": fs.get("subject"),
                "reply_to": pm.get("reply_to"), "sender": fs.get("sender"),
                "message_stream": fs.get("message_stream")}
    return correlation, expected


def reconcile_postmark_recovery(conn, action_request_id: str, *, transport: PostmarkTransport,
                                actual_cost=0, actor: str = "market"):
    """Explicit recovery for a Postmark action that FAILED CLOSED on an ambiguous send (status
    RECOVERY_REQUIRED). Search provider state by the frozen correlation: EXACTLY ONE fully-compliant
    message -> capture it and complete through the normal deterministic verifier (one proof); ZERO
    (an empty, eventually-consistent search is NOT proof of absence) or MULTIPLE (an ambiguous
    duplicate) -> remain RECOVERY_REQUIRED, never redispatch and never pick arbitrarily. Ordinary
    execute_action can never perform this — a RECOVERY_REQUIRED action is not auto-claimable."""
    status = execution.get_status(conn, action_request_id)
    if status != "RECOVERY_REQUIRED":
        return {"outcome": "not_recovery_required", "status": status}
    correlation, expected = _frozen_expected(conn, action_request_id)
    try:
        candidates = [m for m in transport.find_outbound_by_correlation(dict(correlation))
                      if _message_matches_frozen(m, expected)]
    except PostmarkReconcileUnknown:
        # Provider read-back undetermined -> cannot prove presence OR absence. Stay fail-closed;
        # never redispatch. The action remains RECOVERY_REQUIRED for a later reconcile.
        return {"outcome": "still_ambiguous", "reason": "provider_state_unknown"}
    if len(candidates) > 1:
        return {"outcome": "duplicate_detected"}          # ambiguous duplicate -> stay fail-closed
    if not candidates:
        return {"outcome": "still_ambiguous"}             # zero != proven absence -> stay fail-closed
    message_id = str(candidates[0]["MessageID"])
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM execution_attempt WHERE action_request_id = %s "
                    "ORDER BY attempt_number DESC LIMIT 1", (action_request_id,))
        attempt_id = cur.fetchone()[0]
    raw_payload = {"worker_kind": POSTMARK_VERIFIER_KIND, "worker_version": "postmark-recovery",
                   "reported_outcome": "success",
                   "structured_output": {"message_id": message_id, "sent": True, "delivered": True},
                   "artifacts": [], "failure_metadata": {}}
    execution.record_execution_result(conn, action_request_id, external_result_id=message_id,
                                      reported_outcome="success", raw_payload=raw_payload, attempt_id=attempt_id)
    with db.transaction(conn) as cur:
        execution._set_status(cur, action_request_id, "RUNNING", actor=actor, reason="recovery.reconciled")
    outcome = verify_postmark_action(conn, action_request_id, transport=transport,
                                     actual_cost=actual_cost, actor=actor)
    return {"outcome": "reconciled", "message_id": message_id,
            "verified": bool(getattr(outcome, "verified", False))}


# --------------------------------------------------------------------------
# provider-event normalization -> existing market_observation
# --------------------------------------------------------------------------
class WebhookAuthError(MarketAuthorityError):
    """Webhook Basic-Auth failed — a DISTINCT authentication failure (the ingress maps it to HTTP
    401), separate from a provenance/reconciliation rejection."""


def _authenticate(source: PostmarkSource, auth_header: str) -> None:
    expected = "Basic " + base64.b64encode(f"{source.webhook_user}:{source.webhook_secret}".encode()).decode()
    # constant-time comparison (avoid a timing side-channel on the shared webhook secret)
    if not auth_header or not hmac.compare_digest(str(auth_header), expected):
        raise WebhookAuthError("Postmark webhook authentication failed (Basic-Auth mismatch)")


def _spec_for_message(conn, message_id: str, transport: PostmarkTransport):
    """Bind a provider event to the EXACT market action by requiring its MessageID to equal the
    proven outbound MessageID (execution_result.external_result_id) of that action's VERIFIED
    MARKET_ACTION proof — provider Metadata is only a secondary cross-check, never the primary key.
    So a different provider message carrying copied canonical Metadata is rejected."""
    msg = transport.get_outbound_message(message_id)   # independent provider reconciliation
    if msg is None:
        raise MarketAuthorityError(f"Postmark MessageID {message_id!r} has no reconcilable outbound record")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ms.id, ms.venture_id FROM market_action_spec ms "
            "JOIN proof_receipt pr ON pr.action_request_id = ms.action_request_id "
            "  AND pr.verification_type = 'MARKET_ACTION' AND pr.result = 'VERIFIED' "
            "JOIN execution_result er ON er.id = pr.execution_result_id "
            "WHERE er.external_result_id = %s AND ms.channel_kind = %s",
            (message_id, POSTMARK_CHANNEL))
        row = cur.fetchone()
    if row is None:
        raise MarketAuthorityError(
            "event MessageID is not the exact proven outbound MessageID of any Postmark action")
    spec_id, venture_id = str(row[0]), str(row[1])
    if str(msg.get("Metadata", {}).get("market_action_spec")) != spec_id:
        raise MarketAuthorityError("provider Metadata does not match the exact proven action")
    return spec_id, venture_id


def _frozen_postmark_for_spec(conn, spec_id) -> dict:
    """The immutable Postmark contract block (frozen source + recipient_hash) for a market action."""
    with conn.cursor() as cur:
        cur.execute("SELECT es.expected_output_contract FROM execution_spec es "
                    "JOIN market_action_spec ms ON ms.action_request_id = es.action_request_id WHERE ms.id = %s",
                    (spec_id,))
        row = cur.fetchone()
    return dict((row[0] or {}).get("postmark", {})) if row else {}


def _observation_provider_state(transport, message_id, frozen_source):
    """Trusted attestation for an observation, only if the attested provider server matches the
    action's frozen approved source (a runtime source for another server cannot confer REAL)."""
    ps = _trusted_provider_state(transport, message_id)
    if ps is not None and (str(getattr(ps, "server_id", "")) != str(frozen_source.get("postmark_server_id"))
                           or str(getattr(ps, "message_stream", "")) != str(frozen_source.get("message_stream"))):
        return None
    return ps


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
    spec_id, venture_id = _spec_for_message(conn, message_id, transport)
    external_event_id = f"{record_type}:{message_id}"
    res = record_market_observation(
        conn, spec_id, external_event_id=external_event_id, observation_type=otype,
        channel_kind=POSTMARK_CHANNEL, raw_evidence=dict(raw_event), actor=actor)
    # attest against the action's FROZEN source identity (None for fake/subclass/other-server)
    fs = dict(_frozen_postmark_for_spec(conn, spec_id).get("source", {}))
    provider_state = _observation_provider_state(transport, message_id, fs)
    origin_mod.record_observation_origin(
        conn, res.market_observation_id, provider_state=provider_state, provider_kind="postmark",
        source_instance_ref=str(fs.get("source_identity")), provider_event_ref=external_event_id, actor=actor)
    return res


def _normalize_email(value) -> str:
    """Deterministic address extraction: 'Name <a@b>' or 'a@b' -> 'a@b' (lowercased/stripped)."""
    import re
    if not value:
        return ""
    m = re.search(r"<([^>]+)>", str(value))
    return (m.group(1) if m else str(value)).strip().lower()


def ingest_postmark_reply(conn, raw_inbound: dict, *, source: PostmarkSource, auth_header: str,
                          transport: PostmarkTransport, actor: str = "market"):
    """Authenticate a Postmark Inbound reply, correlate it to exactly one market action by the
    structural MailboxHash token, AND prove the inbound sender is the exact authorized recipient
    FROZEN for the executed action (a later runtime resolver cannot redefine who the buyer was; a
    reply from any other sender is rejected). Reply text remains untrusted DATA."""
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
    spec_id, venture_id = match[0]
    frozen = _frozen_postmark_for_spec(conn, spec_id)
    # buyer attribution against the FROZEN recipient identity of the executed action (immutable)
    if not frozen.get("recipient_hash") or _recipient_hash(raw_inbound.get("From")) != str(frozen["recipient_hash"]):
        raise MarketAuthorityError("inbound reply sender is not the frozen authorized recipient")
    external_event_id = f"Inbound:{raw_inbound.get('MessageID')}"
    res = record_market_observation(
        conn, spec_id, external_event_id=external_event_id, observation_type="REPLIED",
        channel_kind=POSTMARK_CHANNEL, raw_evidence=dict(raw_inbound), actor=actor)
    # attest by reconciling the OUTBOUND action's provider message against the frozen source
    with conn.cursor() as cur:
        cur.execute("SELECT er.external_result_id FROM execution_result er "
                    "JOIN proof_receipt pr ON pr.execution_result_id = er.id "
                    "JOIN market_action_spec ms ON ms.action_request_id = pr.action_request_id "
                    "WHERE ms.id = %s AND pr.verification_type = 'MARKET_ACTION' AND pr.result = 'VERIFIED' "
                    "ORDER BY pr.created_at DESC, pr.id DESC LIMIT 1", (spec_id,))
        orow = cur.fetchone()
    fs = dict(frozen.get("source", {}))
    provider_state = _observation_provider_state(transport, orow[0], fs) if orow else None
    origin_mod.record_observation_origin(
        conn, res.market_observation_id, provider_state=provider_state, provider_kind="postmark",
        source_instance_ref=str(fs.get("source_identity")), provider_event_ref=external_event_id, actor=actor)
    return res


# --------------------------------------------------------------------------
# production HTTP transport (stdlib only; NOT exercised in the Slice-2 suite)
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# real-provider attestation (trusted provider-path) + the low-level HTTP boundary
# --------------------------------------------------------------------------
_ATTEST_KEY = object()   # module-private; only this module can construct a real attestation


class _PostmarkVerifiedProviderState:
    """Trusted proof that the genuine production Postmark HTTP path actually retrieved and
    reconciled provider state for a MessageID. It is NOT a caller/worker value: the private key +
    exact-type guard stop REAL provenance from being forged from a runtime type, a subclass, a
    fabricated object, or the public API. Under the current in-process trust model this is a
    kernel/provider-path boundary (specialist workers hold no DB authority), NOT a language-level
    guarantee against arbitrary in-process malicious code — that sandbox is a later-gate concern."""

    __slots__ = ("message_id", "server_id", "message_stream", "delivery_type")

    def __init__(self, _key, *, message_id, server_id, message_stream, delivery_type):
        if _key is not _ATTEST_KEY:
            raise RuntimeError("_PostmarkVerifiedProviderState requires the trusted provider-path key")
        self.message_id = message_id
        self.server_id = server_id            # actual Postmark Server ID (GET /server)
        self.message_stream = message_stream
        self.delivery_type = delivery_type    # actual DeliveryType — a REAL attestation is only Live


def _http_request(method: str, url: str, *, headers: dict, body: bytes = None,
                  timeout: float = POSTMARK_HTTP_TIMEOUT_SECONDS):  # pragma: no cover - network
    """The SOLE network boundary. Returns (status_code, parsed_json_or_None) for any HTTP RESPONSE
    (2xx/4xx/5xx). A transport/timeout/network fault — where there is NO usable provider response —
    raises ``PostmarkReconcileUnknown`` (fail closed; never a status/empty that could look like a
    definitive outcome). Explicit bounded timeout; no unbounded call. Tests stub THIS function."""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, (json.loads(raw) if raw else None)
            except Exception:  # noqa: BLE001 - a 2xx with an unparseable body is UNDETERMINED
                return resp.status, None
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read())
        except Exception:  # noqa: BLE001
            parsed = None
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # No usable provider response (timeout/DNS/connection reset): UNKNOWN, never empty. Fail closed.
        raise PostmarkReconcileUnknown(f"Postmark transport failure: {type(exc).__name__}") from exc


class PostmarkHttpTransport:
    """Real Postmark transport over stdlib urllib (no third-party dependency). All network I/O
    goes through the module-level ``_http_request`` (the only place tests stub); the token is a
    trusted runtime injection, never persisted in canonical state. Its ``reconcile`` produces the
    trusted attestation that the origin kernel treats as REAL_PROVIDER evidence."""

    _API = "https://api.postmarkapp.com"

    def __init__(self, server_token: str):
        self._token = server_token

    def _headers(self) -> dict:
        return {"X-Postmark-Server-Token": self._token, "Accept": "application/json",
                "Content-Type": "application/json"}

    def send_email(self, *, message_stream, sender, to, subject, text_body, reply_to, metadata) -> str:
        import json
        body = json.dumps({"From": sender, "To": to, "Subject": subject, "TextBody": text_body,
                           "ReplyTo": reply_to, "Metadata": metadata, "MessageStream": message_stream}).encode()
        status, data = _http_request("POST", f"{self._API}/email", headers=self._headers(), body=body)
        mid = (data or {}).get("MessageID")
        if status is not None and 200 <= status < 300 and mid:
            return str(mid)
        if status is not None and 400 <= status < 500:
            # a received 4xx = deterministic client rejection: the send was NOT accepted (no effect)
            raise PostmarkSendRejected(f"Postmark rejected the send (status={status})")
        # 5xx / unusable response / accepted-without-id: the effect MAY have occurred -> ambiguous
        raise AmbiguousExternalEffectError(f"Postmark send outcome is ambiguous (status={status})")

    def get_server_state(self) -> PostmarkServerState:
        """The actual Postmark server facts for the runtime token: GET /server -> ("ID","DeliveryType")
        (server-scoped, proves which server the token belongs to AND whether it is Live vs Sandbox).
        Non-secret; no token returned into evidence. A non-200 / malformed response is UNKNOWN (raise),
        never a fabricated 'None/None' server state that could pass or fail a check by accident."""
        status, data = _http_request("GET", f"{self._API}/server", headers=self._headers())
        if status != 200 or not isinstance(data, dict):
            raise PostmarkReconcileUnknown(f"Postmark GET /server undetermined (status={status})")
        return PostmarkServerState(server_id=str(data.get("ID")), delivery_type=str(data.get("DeliveryType")))

    def get_outbound_message(self, message_id):
        status, data = _http_request("GET", f"{self._API}/messages/outbound/{message_id}/details",
                                     headers=self._headers())
        if status == 404:
            return None                        # DEFINITIVE: unknown MessageID -> no reconcilable record
        if status != 200 or not isinstance(data, dict):
            # 5xx / other 4xx / unparseable -> UNDETERMINED. Never collapse to 'no record'.
            raise PostmarkReconcileUnknown(f"Postmark outbound details undetermined (status={status})")
        recipients = data.get("Recipients") or [r.get("Email") for r in (data.get("To") or []) if isinstance(r, dict)]
        # Reply-To: prefer the structured field; else parse the provider's raw message headers
        # (stdlib email, no dependency) — never trust what the worker CLAIMED it sent.
        reply_to = data.get("ReplyTo")
        if not reply_to and data.get("Body"):
            import email
            reply_to = email.message_from_string(str(data.get("Body"))).get("Reply-To")
        return {
            "MessageID": data.get("MessageID"), "MessageStream": data.get("MessageStream"),
            "From": data.get("From"), "To": recipients[0] if recipients else data.get("To"),
            "Subject": data.get("Subject"), "TextBody": data.get("TextBody"),
            "ReplyTo": reply_to, "Sandboxed": data.get("Sandboxed"),
            "Metadata": data.get("Metadata", {}), "Status": data.get("Status")}

    def find_outbound_by_correlation(self, correlation: dict) -> list:
        """Search Postmark's outbound messages by the frozen correlation Metadata (the unique action
        identity), then load each candidate's full details for exact-identity reconciliation. A bare
        metadata match is never trusted alone — the caller re-checks the full frozen contract."""
        import urllib.parse
        params = "&".join(f"metadata_{k}={urllib.parse.quote(str(v))}" for k, v in correlation.items())
        status, data = _http_request(
            "GET", f"{self._API}/messages/outbound?count=100&offset=0&{params}", headers=self._headers())
        # ONLY a valid HTTP 200 with a Messages array is a DEFINITIVE search result (possibly empty =
        # genuine no-match). A 5xx / non-200 / malformed payload is UNDETERMINED -> raise, never empty
        # (an empty-from-error would look like 'no prior send' and risk a duplicate send).
        if status != 200 or not isinstance(data, dict) or "Messages" not in data:
            raise PostmarkReconcileUnknown(f"Postmark outbound search undetermined (status={status})")
        out = []
        for m in data.get("Messages", []):
            mid = m.get("MessageID")
            if not mid:
                continue
            full = self.get_outbound_message(mid)   # strict: raises UNKNOWN on 5xx, None only on a real 404
            if full is not None:
                out.append(full)
        return out

    def reconcile(self, message_id: str):
        """Independently retrieve provider state (real HTTP) and, on success, attest it with the
        ACTUAL server id + DeliveryType (GET /server) and message stream. A non-Live server OR a
        Sandboxed message can NEVER produce a REAL attestation. Returns a trusted state or None."""
        server = self.get_server_state()
        msg = self.get_outbound_message(message_id)
        if msg is None or not msg.get("MessageID"):
            return None
        if str(server.delivery_type) != REQUIRED_DELIVERY_TYPE or msg.get("Sandboxed") is True:
            return None                          # Sandbox / non-Live provider state is never REAL
        return _PostmarkVerifiedProviderState(
            _ATTEST_KEY, message_id=str(msg.get("MessageID")), server_id=str(server.server_id),
            message_stream=str(msg.get("MessageStream")), delivery_type=str(server.delivery_type))


def _trusted_provider_state(transport, message_id):
    """Produce a REAL attestation ONLY when the transport is EXACTLY the production
    ``PostmarkHttpTransport`` (a subclass is not trusted) AND its real reconciliation succeeds.
    A fake/subclass/forged transport yields None -> SIMULATED."""
    if type(transport) is not PostmarkHttpTransport:
        return None
    return transport.reconcile(message_id)
