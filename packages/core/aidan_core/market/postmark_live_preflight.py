"""ZERO-SEND live-credential preflight for the Postmark owner-ingress boundary (Gate 8).

Before any future owner-ingress SEND is authorized, this proves — with a single GET /server read and
NOTHING else — that the configured ``POSTMARK_SERVER_TOKEN`` secret can actually authenticate to the
expected Postmark server AND that the server satisfies the live-send contract (exact Server ID +
DeliveryType == Live). It issues NO POST /email and touches NO mutation endpoint: the transport is
wrapped in a read-only seam that exposes only ``get_server_state`` (no ``send_email``), so a send is
structurally impossible.

A 401/403 is reported as a deterministic AUTH_FAILURE (the exact defect that silently became
RECOVERY_REQUIRED on owner-ingress smoke #1), never as ambiguity. Sanitized evidence only — no token,
no raw provider body.
"""
from __future__ import annotations

import json
import os

from . import postmark as pm
from . import postmark_smoke_spec as spec
from .postmark import PostmarkAuthError, PostmarkReconcileUnknown, REQUIRED_DELIVERY_TYPE
from .postmark_recovery_readonly import _ReadOnlyPostmark

CONFIRM_TOKEN = "RUN_POSTMARK_LIVE_PREFLIGHT"
ACCEPTED_SHA_ENV = "POSTMARK_PREFLIGHT_ACCEPTED_SHA"


def run_postmark_live_preflight(*, server_id: str, token: str = None, transport=None) -> dict:
    """Prove the secret authenticates to the expected Live Postmark server with ONE GET /server read.

    Classification in {PASS, AUTH_FAILURE, CONFIG_FAILURE, PROVIDER_UNAVAILABLE}. PASS requires a 200
    with provider contact OBSERVED, the exact expected Server ID, and DeliveryType == Live. No send is
    ever issued."""
    raw = transport if transport is not None else pm.PostmarkHttpTransport(
        token if token is not None else os.environ.get(spec.TOKEN_ENV, ""))
    ro = _ReadOnlyPostmark(raw)                 # exposes get_server_state; has NO send_email

    ev = {"preflight": "gate8-postmark-live-preflight", "expected_server_id": str(server_id),
          "provider_contact": "UNKNOWN", "classification": "PROVIDER_UNAVAILABLE",
          "sends_issued": 0, "read_only": True, "repo_sha": os.environ.get("GITHUB_SHA", "local")}
    try:
        server = ro.get_server_state()          # the ONLY provider call — a pure read
    except PostmarkAuthError as exc:
        ev.update(classification="AUTH_FAILURE", reason="unauthorized",
                  transport_fault={"http_status": getattr(exc, "http_status", None), "fault_kind": "auth"})
        return _finalize(ev)
    except PostmarkReconcileUnknown as exc:
        ev.update(classification="PROVIDER_UNAVAILABLE", reason="provider_unreachable_server",
                  transport_fault={"http_status": getattr(exc, "http_status", None),
                                   "fault_kind": getattr(exc, "fault_kind", None)})
        return _finalize(ev)

    ev["provider_contact"] = "OBSERVED"
    ev["server_identity_match"] = str(server.server_id) == str(server_id)
    ev["server_is_live"] = str(server.delivery_type) == REQUIRED_DELIVERY_TYPE
    if not ev["server_identity_match"]:
        ev.update(classification="CONFIG_FAILURE", reason="wrong_server_id")
    elif not ev["server_is_live"]:
        ev.update(classification="CONFIG_FAILURE", reason="server_not_live")
    else:
        ev.update(classification="PASS", reason="authenticated_live_server")
    return _finalize(ev)


def _finalize(ev: dict) -> dict:
    token = os.environ.get(spec.TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    return ev


def main() -> int:
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"preflight": "gate8-postmark-live-preflight", "result": "CONFIRM_REQUIRED"}))
        return 2
    accepted = os.environ.get(ACCEPTED_SHA_ENV)
    if not accepted:
        print(json.dumps({"preflight": "gate8-postmark-live-preflight", "result": "CONFIG_ERROR",
                          "reason": ACCEPTED_SHA_ENV}))
        return 2
    if os.environ.get("GITHUB_SHA") and accepted != os.environ["GITHUB_SHA"]:
        print(json.dumps({"preflight": "gate8-postmark-live-preflight", "result": "SHA_MISMATCH"}))
        return 3
    for env in (spec.TOKEN_ENV, spec.SERVER_ID_ENV):
        if not os.environ.get(env):
            print(json.dumps({"preflight": "gate8-postmark-live-preflight", "result": "CONFIG_ERROR",
                              "reason": env}))
            return 2
    try:
        ev = run_postmark_live_preflight(server_id=os.environ[spec.SERVER_ID_ENV])
    except Exception as exc:  # sanitized; never a raw traceback / provider body
        print(json.dumps({"preflight": "gate8-postmark-live-preflight", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    print(json.dumps(ev, sort_keys=True))
    ok = (ev.get("classification") == "PASS" and ev.get("secret_leak_check") == "PASS"
          and ev.get("sends_issued") == 0)
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
