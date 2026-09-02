"""Fly Machines API transport seam (Gate 6 real-deploy readiness).

The Fly WorkerAdapter and observer talk to the Fly Machines API ONLY through a ``FlyTransport``
callable — an injectable seam so the whole deploy path is proven deterministically with fakes and no
network. A real ``HttpFlyTransport`` (stdlib ``urllib`` only, no dependency) is used solely at live
smoke time. Nothing here holds canonical authority or persists a credential; the bearer token is
passed per call and never logged.

Official Fly Machines contract (https://fly.io/docs/machines/api/machines-resource/):
  base       ``https://api.machines.dev/v1``            Authorization: ``Bearer <token>``
  create     POST   /v1/apps/{app}/machines             body: {name, region, config:{image}}
  get        GET    /v1/apps/{app}/machines/{id}         -> {id, instance_id, state, image_ref:{digest,...}}
  list       GET    /v1/apps/{app}/machines
  wait       GET    /v1/apps/{app}/machines/{id}/wait?state=started&timeout=<s>
  destroy    DELETE /v1/apps/{app}/machines/{id}

The PRE_SEND / POST_SEND phase on a transport failure is load-bearing: PRE_SEND means the request
was provably NOT transmitted (no external effect possible -> a clean no-effect failure); POST_SEND
means it MAY have reached Fly (ambiguous -> reconcile, never blind-retry).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

FLY_API_BASE = "https://api.machines.dev/v1"
_MAX_BODY_BYTES = 65536   # bounded response body; never store/log more

# Fly retains DELETED machine records as HTTP 200 with a terminal state (it does NOT return 404).
# A machine in one of these terminal states is ABSENT for our purposes — it is not running and not
# billing. This is the ONE shared absence semantic (cleanup, recovery, observer all use it).
MACHINE_ABSENT_STATES = ("destroyed",)
# States a machine can never leave to become a healthy 'started' target (stop bounded polling early).
MACHINE_TERMINAL_STATES = ("destroyed", "stopped", "failed")


def is_machine_absent(resp) -> bool:
    """True iff the provider confirms the exact machine is absent: HTTP 404, OR HTTP 200 with a
    terminal ``state`` (a retained destroyed record). Fail-closed: None/other -> not absent."""
    if resp is None:
        return False
    if resp.status == 404:
        return True
    if resp.status == 200 and (resp.body or {}).get("state") in MACHINE_ABSENT_STATES:
        return True
    return False

PHASE_PRE_SEND = "PRE_SEND"     # request provably not transmitted -> no external effect
PHASE_POST_SEND = "POST_SEND"   # request may have reached the provider -> effect ambiguous


@dataclass(frozen=True)
class FlyResponse:
    status: int
    body: dict[str, Any]        # parsed JSON (bounded), or {} when absent/unparseable


class FlyTransportError(Exception):
    """A transport-level failure (connect/timeout/read). ``phase`` says whether the request could
    have reached Fly. Carries only a bounded static reason, never raw provider bytes."""

    def __init__(self, reason: str, *, phase: str):
        super().__init__(reason)
        self.reason = str(reason)
        self.phase = phase       # PHASE_PRE_SEND | PHASE_POST_SEND


class HttpFlyTransport:
    """Real stdlib transport (used only at live smoke time). Conservative phase classification: a
    failure while establishing the connection is PRE_SEND (no effect); any timeout/failure after the
    request body is written is POST_SEND (ambiguous), so we never assume 'no effect' when unsure."""

    def __init__(self, base_url: str = FLY_API_BASE):
        self.base_url = base_url.rstrip("/")

    def __call__(self, method: str, path: str, *, token: str, body: Optional[dict] = None,
                 timeout: float = 60.0) -> FlyResponse:
        import urllib.error
        import urllib.request

        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (fixed https host)
                raw = resp.read(_MAX_BODY_BYTES + 1)
                return FlyResponse(resp.status, _parse_bounded(raw))
        except urllib.error.HTTPError as exc:
            # An HTTP status response IS a provider acknowledgement (the request reached Fly).
            try:
                raw = exc.read(_MAX_BODY_BYTES + 1)
            except Exception:  # noqa: BLE001
                raw = b""
            return FlyResponse(exc.code, _parse_bounded(raw))
        except (TimeoutError, urllib.error.URLError) as exc:
            # URLError before an HTTP response: could be pre-connect (no effect) or mid-flight. We
            # cannot always tell; classify conservatively as POST_SEND unless it is clearly a
            # name/connection failure with no bytes sent.
            reason = type(exc).__name__
            phase = PHASE_PRE_SEND if _is_pre_connect(exc) else PHASE_POST_SEND
            raise FlyTransportError(reason, phase=phase) from exc


def _parse_bounded(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_BODY_BYTES:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _is_pre_connect(exc: Exception) -> bool:
    import socket
    import urllib.error
    reason = getattr(exc, "reason", None)
    # DNS failure / connection refused before any bytes are written -> provably no effect.
    if isinstance(reason, socket.gaierror):
        return True
    if isinstance(reason, ConnectionRefusedError):
        return True
    return False
