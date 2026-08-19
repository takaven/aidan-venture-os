"""Deterministic controlled market channel (Gate 7 Slice 2).

A venture- and source-scoped LOCAL outbox — real enough to inspect independently, but
NOT a live channel: no network, no provider account, no credentials. A channel worker
materializes the exact authorized outbound envelope + a deterministic acceptance id into
the outbox; the market-action verifier reads the outbox back and checks it against the
frozen ``market_action_spec``. This proves the market-action architecture (the exact
authorized action occurred in the controlled channel), NOT real external delivery.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Optional

_MARKET_ROOT = Path(tempfile.gettempdir()) / "aidan-market"


def source_instance_ref(venture_id, channel_kind) -> str:
    """Canonical channel/source-instance identity (venture-scoped) for Alpha."""
    return f"{channel_kind}:{venture_id}"


def outbox_path(venture_id, channel_kind, action_request_id) -> str:
    """Deterministic, venture/source-scoped outbox for one market action."""
    si = source_instance_ref(venture_id, channel_kind).replace(":", "_")
    return str(_MARKET_ROOT / str(venture_id) / si / "outbox" / str(action_request_id))


def acceptance_id(source_instance, attempt_id) -> str:
    """Deterministic acceptance identity attributable to the exact attempt."""
    return f"local-market:{source_instance}:{attempt_id}"


def content_sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _envelope_file(outbox: str) -> Path:
    return Path(outbox) / "envelope.json"


def _acceptance_file(outbox: str) -> Path:
    return Path(outbox) / "acceptance.txt"


def write_envelope(outbox: str, envelope: dict, accept: str) -> None:
    """Materialize the outbound envelope + acceptance (used by a channel WorkerAdapter)."""
    Path(outbox).mkdir(parents=True, exist_ok=True)
    _envelope_file(outbox).write_text(json.dumps(envelope, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    _acceptance_file(outbox).write_text(accept, encoding="utf-8")


def read_envelope(outbox: str) -> Optional[dict]:
    f = _envelope_file(outbox)
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def read_acceptance(outbox: str) -> Optional[str]:
    f = _acceptance_file(outbox)
    return f.read_text(encoding="utf-8") if f.is_file() else None
