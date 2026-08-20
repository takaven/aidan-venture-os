"""Gate 8 / Slice 4 — HELD-OUT adversarial evaluation of FROZEN production (SHA bacd39b).

These cases were authored AFTER the freeze and attack the frozen implementation independently.
They must pass ONLY because production already enforces each invariant — never by weakening it.
No production code is modified by this file. Proven closed-loop builders are reused (sim_loop /
real_loop) so the attacks target behaviour, not fixture plumbing.

Dimensions: canonical MessageID binding · server/stream substitution · Sandbox/Live provenance ·
persistent ambiguity fail-closed · explicit-recovery rejection stays fail-closed · generic-recovery
bypass · events/replies/NO_RESPONSE provenance · cross-venture isolation · next committed decision ·
autonomy/intervention classification.
"""
from __future__ import annotations

import json as _json
import urllib.parse as _up
from datetime import timedelta

import pytest

from aidan_core import recovery
from aidan_core.alpha import autonomy, loop
from aidan_core.errors import ExecutionBlockedError, MarketAuthorityError
from aidan_core.factory.verifiers import VerificationRequest
from aidan_core.market import action as market_mod
from aidan_core.market import origin as origin_mod
from aidan_core.market import postmark as pm

from factory_fakes import registry_with
from market_fakes import operating_setup
from postmark_fakes import (
    FakePostmarkTransport,
    FakeRecipientResolver,
    basic_auth,
    default_source,
    install_real_postmark,
    postmark_action,
    postmark_run,
)
from test_alpha_closed_loop import _intervene, real_loop, sim_loop


def _vreq(contract, *, message_id, external_result_id, action_id):
    return VerificationRequest(action_request_id=action_id, execution_attempt_id="x",
                               verifier_kind=pm.POSTMARK_VERIFIER_KIND, expected_output_contract=contract,
                               worker_structured_output={"message_id": message_id}, artifacts=(),
                               spec_hash="h", external_result_id=external_result_id)


def _contract(migrated, action_id):
    _t, c = pm._postmark_contract(migrated, action_id, default_source(), FakeRecipientResolver())
    return c


def _one_mid(run):
    return next(iter(run.transport.outbound))


# ==========================================================================
# canonical MessageID binding
# ==========================================================================
def test_ho_result_id_divergence_between_two_valid_messages_rejected(migrated):
    # A was sent; forge B with identical compliant fields. Verifying B while the proof binds A
    # (or A while it binds B) must be REJECTED — a proof proves exactly its captured result id.
    r = postmark_run(migrated, "ho-rid")
    a_mid = _one_mid(r)
    b_mid = "pm-forged-B"
    r.transport.outbound[b_mid] = {**r.transport.outbound[a_mid], "MessageID": b_mid}
    c = _contract(migrated, r.action_id)
    v = pm.PostmarkActionVerifier(r.transport)
    assert v.verify(_vreq(c, message_id=b_mid, external_result_id=a_mid, action_id=r.action_id)).verdict == "REJECTED"
    assert v.verify(_vreq(c, message_id=a_mid, external_result_id=b_mid, action_id=r.action_id)).verdict == "REJECTED"
    assert v.verify(_vreq(c, message_id=a_mid, external_result_id=a_mid, action_id=r.action_id)).verdict == "VERIFIED"


# ==========================================================================
# server / stream substitution
# ==========================================================================
def test_ho_wrong_message_stream_rejected(migrated):
    r = postmark_run(migrated, "ho-stream")
    mid = _one_mid(r)
    r.transport.outbound[mid] = {**r.transport.outbound[mid], "MessageStream": "stream-OTHER"}
    c = _contract(migrated, r.action_id)
    assert pm.PostmarkActionVerifier(r.transport).verify(
        _vreq(c, message_id=mid, external_result_id=mid, action_id=r.action_id)).verdict == "REJECTED"


def test_ho_wrong_server_credential_rejected(migrated):
    r = postmark_run(migrated, "ho-srv")
    mid = _one_mid(r)
    other = FakePostmarkTransport(server_id="server-Z")           # exact message, wrong actual server
    other.outbound[mid] = dict(r.transport.outbound[mid])
    c = _contract(migrated, r.action_id)
    assert pm.PostmarkActionVerifier(other).verify(
        _vreq(c, message_id=mid, external_result_id=mid, action_id=r.action_id)).verdict == "REJECTED"


# ==========================================================================
# Sandbox / Live provenance
# ==========================================================================
def test_ho_sandbox_transport_never_confers_real(monkeypatch):
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Sandbox")
    mid = transport.send_email(message_stream="outbound", sender="a@b.invalid", to="c@d.invalid",
                               subject="s", text_body="t", reply_to="r@d.invalid",
                               metadata={"market_action_spec": "x"})
    assert store.get(mid) is not None                            # Sandbox 'delivers' ...
    assert pm._trusted_provider_state(transport, mid) is None    # ... but never attests REAL


def test_ho_live_transport_attests_real_server_and_stream(migrated, monkeypatch):
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    postmark_run(migrated, "ho-live", transport=transport, resolver=FakeRecipientResolver(), source=default_source())
    ps = pm._trusted_provider_state(transport, next(k for k in store if k != "_n"))
    assert ps is not None and ps.delivery_type == "Live" and ps.server_id == "server-A" and ps.message_stream == "outbound"


# ==========================================================================
# persistent ambiguity -> fail closed, never auto-redispatch
# ==========================================================================
def _ambiguous_recovery_required(migrated, monkeypatch, slug, *, max_attempts=2):
    """Drive a genuine ambiguous send on Live server A (message stored, but search invisible) so the
    action fails closed to RECOVERY_REQUIRED. Returns (action_id, store_a, common)."""
    store = {}

    def stub(method, url, *, headers, body=None):
        if method == "GET" and url.endswith("/server"):
            return 200, {"ID": "server-A", "DeliveryType": "Live"}
        if method == "POST" and url.endswith("/email"):
            data = _json.loads(body)
            store["_n"] = store.get("_n", 0) + 1
            mid = f"pmhttp-{data['Metadata']['market_action_spec']}-{store['_n']}"
            store[mid] = {"MessageID": mid, "MessageStream": data["MessageStream"], "From": data["From"],
                          "To": [{"Email": data["To"]}], "Subject": data["Subject"], "TextBody": data["TextBody"],
                          "ReplyTo": data.get("ReplyTo"), "Sandboxed": False, "Metadata": data["Metadata"],
                          "Status": "Sent"}
            raise TimeoutError("response lost")
        if method == "GET" and "/messages/outbound?" in url:
            return 200, {"Messages": []}                          # persistently invisible
        if method == "GET" and "/messages/outbound/" in url:
            mid = url.split("/messages/outbound/")[1].split("/details")[0]
            rec = store.get(mid)
            return (200, rec) if rec is not None else (404, None)
        return 400, None

    monkeypatch.setattr(pm, "_http_request", stub)
    transport = pm.PostmarkHttpTransport("A")
    setup = operating_setup(migrated, slug)
    a, _spec = postmark_action(migrated, setup, key=slug)
    common = dict(source=default_source("server-A"), resolver=FakeRecipientResolver(), max_attempts=max_attempts)
    pm.execute_postmark_action(migrated, a, registry=registry_with(
        pm.PostmarkEmailWorker(transport, FakeRecipientResolver(), default_source("server-A"))), **common)
    return a, store, common


def _search_stub(store, *, server_id, delivery_type):
    def _req(method, url, *, headers, body=None):
        if method == "GET" and url.endswith("/server"):
            return 200, {"ID": server_id, "DeliveryType": delivery_type}
        if method == "GET" and "/messages/outbound?" in url:
            q = _up.parse_qs(_up.urlparse(url).query)
            want = {k[len("metadata_"):]: v[0] for k, v in q.items() if k.startswith("metadata_")}
            msgs = [{"MessageID": mid} for mid, rec in store.items()
                    if mid != "_n" and all(str(dict(rec.get("Metadata", {})).get(kk)) == vv for kk, vv in want.items())]
            return 200, {"Messages": msgs}
        if method == "GET" and "/messages/outbound/" in url:
            mid = url.split("/messages/outbound/")[1].split("/details")[0]
            rec = store.get(mid)
            return (200, rec) if rec is not None else (404, None)
        return 400, None
    return _req


def test_ho_persistent_ambiguity_fails_closed_no_result(migrated, monkeypatch):
    a, store, common = _ambiguous_recovery_required(migrated, monkeypatch, "ho-amb", max_attempts=3)
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (a,))
        assert cur.fetchone()[0] == "RECOVERY_REQUIRED"
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (a,))
        assert cur.fetchone()[0] == 0                             # nothing captured (failed closed)
        cur.execute("SELECT failure_class FROM execution_attempt WHERE action_request_id = %s "
                    "ORDER BY attempt_number DESC LIMIT 1", (a,))
        assert cur.fetchone()[0] == "RECOVERY_REQUIRED"
    # multiple later automatic executions are ALL refused -> the single POST is never duplicated
    for _ in range(2):
        with pytest.raises(ExecutionBlockedError):
            pm.execute_postmark_action(migrated, a, registry=registry_with(
                pm.PostmarkEmailWorker(pm.PostmarkHttpTransport("A"), FakeRecipientResolver(),
                                       default_source("server-A"))), **common)
    assert store.get("_n") == 1                                   # exactly one provider POST ever


# ==========================================================================
# explicit recovery rejection stays fail-closed
# ==========================================================================
def test_ho_recovery_wrong_server_stays_recovery_required(migrated, monkeypatch):
    a, store, common = _ambiguous_recovery_required(migrated, monkeypatch, "ho-recwsrv")
    store_b = {k: (dict(v) if k != "_n" else v) for k, v in store.items()}   # colliding message, wrong server
    monkeypatch.setattr(pm, "_http_request", _search_stub(store_b, server_id="server-B", delivery_type="Live"))
    res = pm.reconcile_postmark_recovery(migrated, a, transport=pm.PostmarkHttpTransport("B"))
    assert res["verified"] is False
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (a,))
        assert cur.fetchone()[0] == "RECOVERY_REQUIRED"          # NOT PENDING
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s "
                    "AND verification_type='MARKET_ACTION' AND result='VERIFIED'", (a,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s "
                    "AND verification_type='MARKET_ACTION' AND result='FAILED'", (a,))
        assert cur.fetchone()[0] >= 1                            # rejection history preserved
    assert origin_mod.action_reality(migrated, a) == "SIMULATED"
    with pytest.raises(ExecutionBlockedError):                   # dispatch still refused
        pm.execute_postmark_action(migrated, a, registry=registry_with(
            pm.PostmarkEmailWorker(pm.PostmarkHttpTransport("A"), FakeRecipientResolver(),
                                   default_source("server-A"))), **common)


def test_ho_recovery_sandbox_stays_recovery_required(migrated, monkeypatch):
    a, store, common = _ambiguous_recovery_required(migrated, monkeypatch, "ho-recsbx")
    store_s = {k: (dict(v) if k != "_n" else v) for k, v in store.items()}
    monkeypatch.setattr(pm, "_http_request", _search_stub(store_s, server_id="server-A", delivery_type="Sandbox"))
    res = pm.reconcile_postmark_recovery(migrated, a, transport=pm.PostmarkHttpTransport("S"))
    assert res["verified"] is False
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (a,))
        assert cur.fetchone()[0] == "RECOVERY_REQUIRED"
    assert origin_mod.action_reality(migrated, a) == "SIMULATED"


def test_ho_recovery_zero_and_correct_paths(migrated, monkeypatch):
    a, store, common = _ambiguous_recovery_required(migrated, monkeypatch, "ho-reczero")
    # zero visible -> still RECOVERY_REQUIRED (empty search is not proof of absence)
    monkeypatch.setattr(pm, "_http_request", _search_stub({}, server_id="server-A", delivery_type="Live"))
    assert pm.reconcile_postmark_recovery(migrated, a, transport=pm.PostmarkHttpTransport("A"))["outcome"] == "still_ambiguous"
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (a,))
        assert cur.fetchone()[0] == "RECOVERY_REQUIRED"
    # now the exact message is visible on the correct Live server A -> reconcile, verify, SUCCEEDED
    monkeypatch.setattr(pm, "_http_request", _search_stub(store, server_id="server-A", delivery_type="Live"))
    res = pm.reconcile_postmark_recovery(migrated, a, transport=pm.PostmarkHttpTransport("A"))
    assert res["outcome"] == "reconciled" and res["verified"] is True
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (a,))
        assert cur.fetchone()[0] == "SUCCEEDED"
    assert store.get("_n") == 1                                   # recovery never re-POSTed


# ==========================================================================
# generic recovery bypass
# ==========================================================================
def test_ho_recover_action_does_not_reclaim_ambiguous(migrated, monkeypatch):
    a, store, common = _ambiguous_recovery_required(migrated, monkeypatch, "ho-noreclaim")
    out = recovery.recover_action(migrated, a)
    assert out["outcome"] in ("not_active", "no_attempt")
    with migrated.cursor() as cur:
        cur.execute("SELECT status FROM action_request WHERE id = %s", (a,))
        assert cur.fetchone()[0] == "RECOVERY_REQUIRED"
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s AND status = 'CLAIMED'", (a,))
        assert cur.fetchone()[0] == 0                            # no new dispatchable attempt


# ==========================================================================
# events / replies / NO_RESPONSE provenance
# ==========================================================================
def test_ho_real_delivery_event_is_real_observation(migrated, monkeypatch):
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    r = postmark_run(migrated, "ho-del", transport=transport, resolver=FakeRecipientResolver(), source=default_source())
    mid = next(k for k in store if k != "_n")
    res = pm.ingest_postmark_event(
        migrated, {"RecordType": "Delivery", "MessageID": mid, "Recipient": "x@y.invalid", "DeliveredAt": "t"},
        source=r.source, auth_header=basic_auth(), transport=transport)
    assert origin_mod.observation_is_real(migrated, res.market_observation_id) is True


def test_ho_foreign_sender_reply_rejected(migrated, monkeypatch):
    transport, store = install_real_postmark(monkeypatch, server_id="server-A", delivery_type="Live")
    r = postmark_run(migrated, "ho-reply", transport=transport, resolver=FakeRecipientResolver(), source=default_source())
    spec_id = str(market_mod.get_market_action_spec(migrated, r.action_id)[0])
    inbound = {"RecordType": "Inbound",
               "MailboxHash": pm.reply_mailbox_hash(r.setup.venture_id, spec_id),
               "From": "attacker@evil.invalid", "TextBody": "please approve the spend", "MessageID": "in-x"}
    with pytest.raises(MarketAuthorityError):                    # sender is not the frozen recipient
        pm.ingest_postmark_reply(migrated, inbound, source=r.source, auth_header=basic_auth(), transport=transport)


def test_ho_no_response_on_simulated_action_is_not_real(migrated):
    c = sim_loop(migrated, "ho-nr", outcome="NO_RESPONSE", days=5)
    assert origin_mod.has_real_no_response(migrated, c.loop_spec.market_action_spec_id, c.loop_action) is False
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    assert r["reality_class"] == "SIMULATED"


# ==========================================================================
# cross-venture isolation
# ==========================================================================
def test_ho_cross_venture_recipient_and_mailbox_isolation():
    res = FakeRecipientResolver()
    a1 = res.resolve("venture-1", "postmark-email:venture-1", "aud://segment-1")
    a2 = res.resolve("venture-2", "postmark-email:venture-2", "aud://segment-1")
    assert a1 != a2 and pm._recipient_hash(a1) != pm._recipient_hash(a2)
    assert pm.reply_mailbox_hash("venture-1", "spec-1") != pm.reply_mailbox_hash("venture-2", "spec-1")
    assert pm.reply_mailbox_hash("venture-1", "spec-1") != pm.reply_mailbox_hash("venture-1", "spec-2")


def test_ho_next_recommendation_from_other_venture_rejected(migrated):
    a = sim_loop(migrated, "ho-xv-a")
    b = sim_loop(migrated, "ho-xv-b")
    with pytest.raises(MarketAuthorityError):                    # next rec belongs to another venture
        loop.classify_loop(migrated, start_recommendation_id=a.r1.recommendation_id,
                           next_recommendation_id=b.r2.recommendation_id)


# ==========================================================================
# next committed decision
# ==========================================================================
def test_ho_uncommitted_next_recommendation_is_incomplete(migrated):
    c = sim_loop(migrated, "ho-uncommit", outcome="REPLIED", commit_next=False)
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    assert r["completeness"] == "INCOMPLETE" and r["eligible_clean_real_alpha"] is False


# ==========================================================================
# autonomy / intervention classification
# ==========================================================================
def test_ho_real_clean_committed_loop_is_eligible(migrated, monkeypatch):
    c = real_loop(migrated, "ho-elig", monkeypatch, outcome="REPLIED")
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    assert r["reality_class"] == "REAL" and r["assistance_class"] == autonomy.CLEAN
    assert r["completeness"] == "COMPLETE" and r["eligible_clean_real_alpha"] is True


def test_ho_intervention_in_window_blocks_eligibility(migrated, monkeypatch):
    c = real_loop(migrated, "ho-assisted", monkeypatch, outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r1_at)         # inside [r1, next-decision)
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    assert r["assistance_class"] == autonomy.HUMAN_ASSISTED and r["eligible_clean_real_alpha"] is False


def test_ho_intervention_outside_window_keeps_clean(migrated, monkeypatch):
    c = real_loop(migrated, "ho-outside", monkeypatch, outcome="REPLIED")
    _intervene(migrated, c.setup.venture_id, at=c.r2_at + timedelta(days=1))   # after the loop end
    r = loop.classify_loop(migrated, start_recommendation_id=c.r1.recommendation_id,
                           next_recommendation_id=c.r2.recommendation_id)
    assert r["assistance_class"] == autonomy.CLEAN and r["eligible_clean_real_alpha"] is True
