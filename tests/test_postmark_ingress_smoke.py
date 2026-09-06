"""End-to-end owner-controlled Postmark ingress smoke (DB; NO real Postmark) — governed send ->
independent verify, fail-closed on ambiguity, one send max, no lifecycle over-promotion.

Matrix rows E,F,I,J,M,N,O,Q,R,S plus the duplicate-send corridor: provider-uncertainty during the
pre-send reconcile can NEVER issue a send. Uses the in-memory FakePostmarkTransport (Live, no
network); the entrypoint transport is injected.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from aidan_core import execution
from aidan_core.errors import AmbiguousExternalEffectError
from aidan_core.market import postmark_live_smoke as smoke
from aidan_core.market import postmark_smoke_spec as spec
from aidan_core.market.postmark import PostmarkAuthError, PostmarkReconcileUnknown

from postmark_fakes import FakePostmarkTransport

RECIPIENT = "owner@owner.invalid"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(spec.TOKEN_ENV, "FAKE-SERVER-TOKEN-do-not-use")
    monkeypatch.setenv(spec.SERVER_ID_ENV, "server-A")
    monkeypatch.setenv(spec.SENDER_ENV, "alpha@sender.invalid")
    monkeypatch.setenv(spec.INBOUND_DOMAIN_ENV, "reply.invalid")


def _run(conn, transport, *, slug):
    return smoke.run_postmark_ingress_smoke(conn, recipient=RECIPIENT, transport=transport, slug=slug)


class _AmbiguousSend(FakePostmarkTransport):
    def send_email(self, **kw):
        raise AmbiguousExternalEffectError("ambiguous Postmark send")


class _UnknownReconcile(FakePostmarkTransport):
    def find_outbound_by_correlation(self, correlation):
        raise PostmarkReconcileUnknown("provider search 5xx")


class _AuthFailServer(FakePostmarkTransport):
    def get_server_state(self):
        raise PostmarkAuthError("Postmark GET /server unauthorized (status=401)", http_status=401)


class _ServerUnavailable(FakePostmarkTransport):
    def get_server_state(self):
        raise PostmarkReconcileUnknown("Postmark GET /server undetermined (status=503)",
                                       http_status=503, fault_kind="http_status")


class _VerifyAmbiguousOnDetails(FakePostmarkTransport):
    # Send + pre-send reads succeed; only the verifier's per-message details read is undetermined.
    def get_outbound_message(self, message_id):
        raise PostmarkReconcileUnknown("Postmark outbound details undetermined (status=503)",
                                       http_status=503, fault_kind="http_status")


# ==== auth/config taxonomy + true send accounting at the canonical level (A,B,C,D,E,G,H) ===========
def test_A_B_auth_401_is_failed_not_recovery(migrated):
    ev = _run(migrated, _AuthFailServer(server_id="server-A"), slug="pm-auth")
    assert ev["result"] == "FAIL" and ev["failure_reason"] == "AUTH_FAILURE"      # taxonomy
    assert ev["worker_invocations"] == 1 and ev["send_post_invocations"] == 0     # A: worker ran, no POST
    assert execution.get_status(migrated, ev["action_request_id"]) == "FAILED"    # B: NOT RECOVERY_REQUIRED
    assert ev["failure_phase"] == "PRE_SEND_RECONCILE"


def test_C_wrong_server_is_config_failure(migrated):
    ev = _run(migrated, FakePostmarkTransport(server_id="server-OTHER"), slug="pm-wrongsrv")
    assert ev["result"] == "FAIL" and ev["failure_reason"] == "CONFIG_FAILURE"
    assert ev["send_post_invocations"] == 0                                       # zero POST
    assert execution.get_status(migrated, ev["action_request_id"]) == "FAILED"


def test_D_non_live_is_config_failure(migrated):
    ev = _run(migrated, FakePostmarkTransport(server_id="server-A", delivery_type="Sandbox"), slug="pm-sandbox")
    assert ev["result"] == "FAIL" and ev["failure_reason"] == "CONFIG_FAILURE"
    assert ev["send_post_invocations"] == 0
    assert execution.get_status(migrated, ev["action_request_id"]) == "FAILED"


def test_E_pre_send_unavailable_is_failed_not_recovery(migrated):
    ev = _run(migrated, _ServerUnavailable(server_id="server-A"), slug="pm-unavail")
    assert ev["result"] == "FAIL" and ev["failure_reason"] == "PROVIDER_UNAVAILABLE"
    assert ev["send_post_invocations"] == 0                                       # no POST possible
    assert execution.get_status(migrated, ev["action_request_id"]) == "FAILED"    # NOT RECOVERY_REQUIRED


def test_G_ambiguous_post_is_recovery_with_one_send(migrated):
    ev = _run(migrated, _AmbiguousSend(server_id="server-A", delivery_type="Live"), slug="pm-ambig2")
    assert ev["result"] == "RECOVERY_REQUIRED"
    assert ev["send_post_invocations"] == 1                                       # a real POST was issued
    assert execution.get_status(migrated, ev["action_request_id"]) == "RECOVERY_REQUIRED"
    assert ev["failure_phase"] == "POST_SEND_RECONCILE"


def test_H_post_send_verify_ambiguity_preserves_message_id(migrated):
    ev = _run(migrated, _VerifyAmbiguousOnDetails(server_id="server-A", delivery_type="Live"), slug="pm-verifamb")
    assert ev["result"] == "FAIL" and ev["failure_phase"] == "POST_SEND_VERIFY"
    assert ev["send_post_invocations"] == 1 and ev.get("message_id")              # MessageID preserved


# ---- happy path: VERIFIED, one send, no over-promote, proof, bounded spend (Q,R,S) ----
def test_pass_verified_one_send_no_overpromote(migrated):
    fake = FakePostmarkTransport(server_id="server-A", delivery_type="Live")
    ev = _run(migrated, fake, slug="pm-ok")
    assert ev["result"] == "PASS" and ev["market_verdict"] == "VERIFIED"
    assert ev["proof_verification_type"] == "MARKET_ACTION" and ev["proof_result"] == "VERIFIED"  # R
    assert ev["provider_contact_evidence"] == "OBSERVED" and ev["send_effect"] == "OBSERVED"
    assert ev["send_post_invocations"] == 1 and fake._n == 1               # exactly one ACTUAL send POST
    assert ev["worker_invocations"] == 1                                   # worker ran once (distinct count)
    assert ev["lifecycle_over_promoted"] is False and ev["lifecycle_after"] == "OPERATING"  # S
    assert ev["governance_deltas"] == 0 and ev["secret_leak_check"] == "PASS"
    assert Decimal(ev["committed"]) <= spec.CEILING                        # Q


# ---- P: the token never appears in the sanitized evidence ----------------------------
def test_P_no_secret_in_evidence(migrated):
    ev = _run(migrated, FakePostmarkTransport(server_id="server-A"), slug="pm-secret")
    assert "FAKE-SERVER-TOKEN-do-not-use" not in json.dumps(ev) and ev["secret_leak_check"] == "PASS"


# ---- E/F/I/J: ambiguous send -> RECOVERY_REQUIRED, one send, no auto retry ------------
def test_E_ambiguous_send_recovery_required(migrated):
    fake = _AmbiguousSend(server_id="server-A", delivery_type="Live")
    ev = _run(migrated, fake, slug="pm-ambig")
    assert ev["result"] == "RECOVERY_REQUIRED"
    assert execution.get_status(migrated, ev["action_request_id"]) == "RECOVERY_REQUIRED"
    assert ev["lifecycle_over_promoted"] is False                          # no over-promotion


# ---- the duplicate-send corridor is CLOSED: provider-uncertainty NEVER sends ---------
def test_reconcile_unknown_pre_send_never_sends(migrated):
    fake = _UnknownReconcile(server_id="server-A", delivery_type="Live")
    ev = _run(migrated, fake, slug="pm-unknown")
    assert ev["result"] == "RECOVERY_REQUIRED"                             # fail closed
    assert fake._n == 0                                                    # NO send POST issued at all
    assert execution.get_status(migrated, ev["action_request_id"]) == "RECOVERY_REQUIRED"


# ---- M/N/O: main() guards block before any provider mutation --------------------------
def test_N_bad_confirm_blocks(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("CONFIRM", "nope")
    assert smoke.main() == 2 and "CONFIRM_REQUIRED" in capsys.readouterr().out


def test_M_accepted_sha_mismatch_blocks(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("CONFIRM", spec.CONFIRM_TOKEN)
    monkeypatch.setenv(spec.ACCEPTED_SHA_ENV, "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert smoke.main() == 3 and "SHA_MISMATCH" in capsys.readouterr().out


def test_O_missing_token_blocks(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("CONFIRM", spec.CONFIRM_TOKEN)
    monkeypatch.setenv(spec.ACCEPTED_SHA_ENV, "abc")
    monkeypatch.setenv("GITHUB_SHA", "abc")
    monkeypatch.delenv(spec.TOKEN_ENV, raising=False)
    assert smoke.main() == 2 and "CONFIG_ERROR" in capsys.readouterr().out


# ---- spec tamper fails closed before any send ---------------------------------------
def test_spec_tamper_blocks(monkeypatch):
    import copy
    tampered = copy.deepcopy(spec.SMOKE_SPEC)
    tampered["spend_ceiling_usd"] = "1.00"
    monkeypatch.setattr(spec, "SMOKE_SPEC", tampered)
    with pytest.raises(spec.PostmarkSmokeSpecMismatch):
        spec.assert_frozen()
