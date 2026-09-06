"""Sender identity is the parsed MAILBOX, not the raw `From` header representation (DB-free).

Proves the MARKET_ACTION verifier, the reconcile matcher, and the read-only recovery all accept a
provider `From` that formats/wraps/cases the SAME mailbox, while still REJECTING any different sender
mailbox or domain and every other identity divergence (recipient, subject, body, stream, sandbox,
correlation, reply-to, result-id). Uses the same strict `_normalize_email` that already governs
recipient and Reply-To identity — no fuzzy matching, no broadened authority.
"""
from __future__ import annotations

from aidan_core.factory.verifiers import VerificationRequest
from aidan_core.market import postmark as pm
from aidan_core.market import postmark_recovery_readonly as rec
from aidan_core.market import postmark_smoke_spec as smoke_spec
from aidan_core.market.channels import content_sha
from aidan_core.market.postmark import (PostmarkActionVerifier, PostmarkServerState, _message_matches_frozen,
                                        _normalize_email, _recipient_hash)

SERVER = "20453649"
SENDER = "admin@takaven.com"
RECIP = "buyer@customer.example"
SUBJECT = "A frozen subject"
BODY = "A frozen body line.\n"
CORR = {"venture": "v1", "market_action_spec": "m1", "action_request": "a1", "action_spec_hash": "h1"}
REPLY_TO = "reply+abc@reply.example"


def _contract():
    fs = {"postmark_server_id": SERVER, "message_stream": "outbound", "sender": SENDER,
          "subject": SUBJECT, "credential_ref": "cr"}
    market = {"market_action_spec_id": "m1", "action_spec_hash": "h1", "content_hash": content_sha(BODY)}
    postmark = {"correlation": dict(CORR), "source": fs, "recipient_hash": _recipient_hash(RECIP),
                "reply_to": REPLY_TO}
    return {"market": market, "postmark": postmark}


def _msg(mid="mid-1", **over):
    m = {"MessageID": mid, "MessageStream": "outbound", "From": SENDER, "To": RECIP, "Subject": SUBJECT,
         "TextBody": BODY, "ReplyTo": REPLY_TO, "Sandboxed": False, "Metadata": dict(CORR), "Status": "Sent"}
    m.update(over)
    return m


class _Transport:
    def __init__(self, msg, *, server_id=SERVER, delivery_type="Live"):
        self._msg = msg
        self._sid, self._dt = server_id, delivery_type

    def get_outbound_message(self, mid):
        return self._msg if (self._msg and str(self._msg["MessageID"]) == str(mid)) else None

    def get_server_state(self):
        return PostmarkServerState(server_id=self._sid, delivery_type=self._dt)


def _verdict(msg, *, mid="mid-1", claimed="mid-1"):
    req = VerificationRequest(
        action_request_id="a1", execution_attempt_id="att-1", verifier_kind=pm.POSTMARK_VERIFIER_KIND,
        expected_output_contract=_contract(), worker_structured_output={"message_id": claimed},
        artifacts=(), spec_hash="s", external_result_id=mid)
    return PostmarkActionVerifier(_Transport(msg)).verify(req).verdict


# ---- the repair: same mailbox in a provider representation -> VERIFIED ----------------------------
def test_exact_sender_verified():
    assert _verdict(_msg(From="admin@takaven.com")) == "VERIFIED"


def test_display_name_same_mailbox_verified():
    assert _verdict(_msg(From="AIDAN Admin <admin@takaven.com>")) == "VERIFIED"


def test_case_and_whitespace_same_mailbox_verified():
    assert _verdict(_msg(From="  ADMIN@Takaven.com  ")) == "VERIFIED"


# ---- a different mailbox / domain is still REJECTED (no weakening) --------------------------------
def test_different_local_part_rejected():
    assert _verdict(_msg(From="attacker@takaven.com")) == "REJECTED"


def test_different_domain_rejected():
    assert _verdict(_msg(From="admin@evil.example")) == "REJECTED"


def test_display_name_but_different_mailbox_rejected():
    assert _verdict(_msg(From="Admin <attacker@takaven.com>")) == "REJECTED"


# ---- every other identity divergence remains REJECTED --------------------------------------------
def test_wrong_recipient_rejected():
    assert _verdict(_msg(To="someone-else@elsewhere.test")) == "REJECTED"


def test_wrong_subject_rejected():
    assert _verdict(_msg(Subject="Unapproved subject")) == "REJECTED"


def test_altered_body_rejected():
    assert _verdict(_msg(TextBody=BODY + " TAMPERED")) == "REJECTED"


def test_wrong_stream_rejected():
    assert _verdict(_msg(MessageStream="broadcast")) == "REJECTED"


def test_sandboxed_rejected():
    assert _verdict(_msg(Sandboxed=True)) == "REJECTED"


def test_correlation_mismatch_rejected():
    bad = dict(CORR); bad["action_request"] = "OTHER"
    assert _verdict(_msg(Metadata=bad)) == "REJECTED"


def test_reply_to_mismatch_rejected():
    assert _verdict(_msg(ReplyTo="reply+FORGED@reply.example")) == "REJECTED"


def test_result_id_mismatch_rejected():
    # worker claims a different id than the canonical captured result id
    assert _verdict(_msg(mid="mid-1"), mid="mid-1", claimed="mid-OTHER") == "REJECTED"


# ---- the reconcile matcher uses the SAME mailbox rule (duplicate-send parity) ---------------------
def _expected():
    return {"correlation": dict(CORR), "content_hash": content_sha(BODY), "recipient_hash": _recipient_hash(RECIP),
            "subject": SUBJECT, "reply_to": REPLY_TO, "sender": SENDER, "message_stream": "outbound"}


def test_message_matches_frozen_display_name_sender():
    assert _message_matches_frozen(_msg(From="Admin <admin@takaven.com>"), _expected()) is True


def test_message_matches_frozen_different_sender_mailbox():
    assert _message_matches_frozen(_msg(From="attacker@takaven.com"), _expected()) is False


# ---- read-only recovery uses the SAME mailbox rule -----------------------------------------------
def test_recovery_matches_display_name_sender():
    exp = rec.reconstruct_expected(sender=SENDER, recipient=RECIP)
    msg = {"Metadata": {"action_request": "a1"}, "TextBody": smoke_spec.SMOKE_BODY,
           "To": RECIP, "Subject": smoke_spec.SMOKE_SUBJECT, "From": "Admin <admin@takaven.com>",
           "MessageStream": smoke_spec.MESSAGE_STREAM, "Sandboxed": False}
    assert rec._matches(msg, exp, "a1") is True


def test_recovery_rejects_different_sender_mailbox():
    exp = rec.reconstruct_expected(sender=SENDER, recipient=RECIP)
    msg = {"Metadata": {"action_request": "a1"}, "TextBody": smoke_spec.SMOKE_BODY,
           "To": RECIP, "Subject": smoke_spec.SMOKE_SUBJECT, "From": "attacker@takaven.com",
           "MessageStream": smoke_spec.MESSAGE_STREAM, "Sandboxed": False}
    assert rec._matches(msg, exp, "a1") is False


# ---- the diagnostic still distinguishes representational vs true semantic sender divergence -------
def test_normalizer_does_not_collapse_distinct_mailboxes():
    # guard: the normalizer folds only case/whitespace/display-name, never distinct mailbox identities
    assert _normalize_email("admin@takaven.com") != _normalize_email("attacker@takaven.com")
    assert _normalize_email("admin@takaven.com") != _normalize_email("admin@evil.example")
    assert _normalize_email("Admin <admin@takaven.com>") == _normalize_email("admin@takaven.com")
