"""Standalone recovery-only entrypoint for a KNOWN orphan-risk Fly Machine (Gate 6).

This is NOT a deployment path. It exists solely to reconcile a single, already-known Machine that a
failed Stage-C smoke may have left behind (cleanup ambiguous). It performs an independent read, and
at most ONE force DELETE of exactly that Machine under exactly that app, then bounded read-only
confirmation. It never creates a machine, never deploys, never issues a second DELETE, never touches
the app / IPs / volumes, and never retries.

Fail-closed: validates the app + machine-id syntax, fails closed against the accepted-main SHA, and
refuses to delete on any pre-mutation ambiguity or identity mismatch. Sanitized evidence only — no
token, header, or raw provider payload.
"""
from __future__ import annotations

import json
import os
import re

from .fly_live_smoke import TOKEN_ENV, validate_app_name
from .fly_transport import FlyTransportError, HttpFlyTransport, is_machine_absent
from .fly_worker import cleanup_machine

CONFIRM_TOKEN = "RUN_FLY_RECOVERY_ONLY"
APP_ENV = "FLY_RECOVERY_APP"
MACHINE_ENV = "FLY_RECOVERY_MACHINE_ID"
ACCEPTED_SHA_ENV = "FLY_RECOVERY_ACCEPTED_SHA"

_MACHINE_RE = re.compile(r"^[0-9a-f]{6,40}$")   # Fly machine ids are lowercase hex


def validate_machine_id(machine_id: str) -> str:
    if not machine_id or not _MACHINE_RE.match(str(machine_id)):
        raise ValueError("machine_id is not a valid Fly machine id")
    return machine_id


def _finalize(ev):
    token = os.environ.get(TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    ev["actual_provider_billing"] = "UNKNOWN"
    return ev


def run_fly_recovery(*, app, machine_id, transport=None, token=None, timeout=30.0, sleep=None):
    """Reconcile exactly ONE known Machine. Returns sanitized evidence. At most one force DELETE; no
    create/deploy; no second DELETE; no app/IP/volume mutation."""
    validate_app_name(app)
    validate_machine_id(machine_id)
    token = token if token is not None else os.environ.get(TOKEN_ENV)
    transport = transport if transport is not None else HttpFlyTransport()

    ev = {"recovery": "gate8-fly-recovery", "fly_app": app, "machine_id": machine_id,
          "result": None, "delete_issued": False, "cleanup_state": "NOT_ATTEMPTED"}

    # Independent pre-mutation read of the EXACT app + machine.
    try:
        resp = transport("GET", f"/apps/{app}/machines/{machine_id}", token=token, timeout=timeout)
    except FlyTransportError:
        ev.update(result="RECOVERY_AMBIGUOUS_NO_DELETE", reason="pre-delete read unreachable")
        return _finalize(ev)

    if is_machine_absent(resp):
        # 404 OR a retained destroyed record -> already gone; never DELETE again.
        ev.update(result="RECOVERY_ALREADY_CLEAN",
                  pre_cleanup={"runtime_state": (resp.body or {}).get("state")} if resp.status == 200 else {})
        return _finalize(ev)
    if resp.status != 200:
        ev.update(result="RECOVERY_AMBIGUOUS_NO_DELETE", reason=f"pre-delete status {resp.status}")
        return _finalize(ev)

    body = resp.body or {}
    if body.get("id") != machine_id:
        # The app returned a machine whose identity does not match -> contradictory; never delete.
        ev["result"] = "RECOVERY_IDENTITY_MISMATCH"
        return _finalize(ev)

    # Exact machine exists under the exact app. Emit sanitized pre-cleanup state, then ONE force
    # DELETE + bounded read-only confirmation (cleanup_machine issues exactly one DELETE).
    ev["pre_cleanup"] = {"observed_digest": (body.get("image_ref") or {}).get("digest"),
                         "runtime_state": body.get("state")}
    ev["delete_issued"] = True
    state = cleanup_machine(transport, token, app, machine_id, timeout=timeout, sleep=sleep)
    ev["cleanup_state"] = state
    ev["result"] = {"CLEANUP_CONFIRMED": "RECOVERY_CONFIRMED",
                    "CLEANUP_FAILED": "RECOVERY_FAILED"}.get(state, "RECOVERY_AMBIGUOUS")
    return _finalize(ev)


def main() -> int:
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"recovery": "gate8-fly-recovery", "result": "CONFIRM_REQUIRED"}))
        return 2
    accepted = os.environ.get(ACCEPTED_SHA_ENV)
    if not accepted:
        print(json.dumps({"recovery": "gate8-fly-recovery", "result": "CONFIG_ERROR", "reason": ACCEPTED_SHA_ENV}))
        return 2
    if os.environ.get("GITHUB_SHA") and accepted != os.environ["GITHUB_SHA"]:
        print(json.dumps({"recovery": "gate8-fly-recovery", "result": "SHA_MISMATCH"}))
        return 3
    for env in (TOKEN_ENV, APP_ENV, MACHINE_ENV):
        if not os.environ.get(env):
            print(json.dumps({"recovery": "gate8-fly-recovery", "result": "CONFIG_ERROR", "reason": env}))
            return 2
    try:
        ev = run_fly_recovery(app=os.environ[APP_ENV], machine_id=os.environ[MACHINE_ENV])
    except Exception as exc:  # sanitized; never a raw traceback / provider body
        print(json.dumps({"recovery": "gate8-fly-recovery", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    print(json.dumps(ev, sort_keys=True))
    ok = ev.get("result") in ("RECOVERY_ALREADY_CLEAN", "RECOVERY_CONFIRMED") and ev.get("secret_leak_check") == "PASS"
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
