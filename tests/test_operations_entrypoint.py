"""Gate 8 / Slice 5A — operational identity entrypoint (env -> GET /server, no send).

Adversarial coverage of ``operations.resolve_identity_from_env`` / ``operations.main``: reads the
runtime environment, builds the transport from the token, and runs the NON-CONSEQUENTIAL identity
check. All provider I/O is stubbed — NO real network, NO send, NO DB. The raw token must never appear
in the result, an exception, or the printed output.
"""
from __future__ import annotations

import io
import json

import pytest

from aidan_core.errors import ConfigError
from aidan_core.market import operations as ops
from aidan_core.market import postmark as pm

_TOKEN = "SUPER-SECRET-SERVER-TOKEN-1234567890"


class _Stub:
    """Minimal transport double: reports a server state; counts sends (must stay 0)."""

    def __init__(self, token=None, *, server_id="server-A", delivery_type="Live", raises=False):
        self.token = token
        self._server_id = server_id
        self._delivery_type = delivery_type
        self._raises = raises
        self.sends = 0

    def get_server_state(self):
        if self._raises:
            raise ConnectionError(f"boom token={self.token}")   # message deliberately embeds the token
        return pm.PostmarkServerState(server_id=self._server_id, delivery_type=self._delivery_type)

    def send_email(self, **kw):        # pragma: no cover - must never be called
        self.sends += 1
        raise AssertionError("identity entrypoint must never send")


def _factory(**kw):
    holder = {}

    def make(token):
        t = _Stub(token, **kw)
        holder["t"] = t
        return t
    make.holder = holder
    return make


def _env(**over):
    e = {ops.ENV_SERVER_TOKEN: _TOKEN, ops.ENV_SERVER_ID: "server-A", ops.ENV_MESSAGE_STREAM: "outbound"}
    e.update(over)
    return e


# ---- fail closed on missing config ----
def test_missing_token_fails_closed():
    e = _env()
    del e[ops.ENV_SERVER_TOKEN]
    with pytest.raises(ConfigError) as ei:
        ops.resolve_identity_from_env(e, transport_factory=_factory())
    assert _TOKEN not in str(ei.value)


def test_missing_expected_server_id_fails_closed():
    e = _env()
    del e[ops.ENV_SERVER_ID]
    with pytest.raises(ConfigError):
        ops.resolve_identity_from_env(e, transport_factory=_factory())


# ---- identity outcomes ----
def test_live_exact_server_ready():
    r = ops.resolve_identity_from_env(_env(), transport_factory=_factory(server_id="server-A", delivery_type="Live"))
    assert r["ready"] is True and r["server_id"] == "server-A" and r["delivery_type"] == "Live"
    assert r["expected_server_id"] == "server-A" and r["expected_message_stream"] == "outbound"


def test_wrong_server_not_ready():
    r = ops.resolve_identity_from_env(_env(), transport_factory=_factory(server_id="server-Z", delivery_type="Live"))
    assert r["ready"] is False and r["checks"]["SERVER_IDENTITY"] is False


def test_sandbox_not_ready():
    r = ops.resolve_identity_from_env(_env(), transport_factory=_factory(server_id="server-A", delivery_type="Sandbox"))
    assert r["ready"] is False and r["checks"]["LIVE_PROVIDER"] is False


def test_wrong_stream_not_ready():
    # the configured source stream diverges from the expected/intended stream -> not ready
    src = pm.PostmarkSource(postmark_server_id="server-A", message_stream="broadcast", sender="a@b.invalid",
                            default_subject="s", inbound_domain="reply.invalid", credential_ref="secret://postmark/alpha")
    r = ops.resolve_identity_from_env(_env(), transport_factory=_factory(), source=src)
    assert r["ready"] is False and r["checks"]["MESSAGE_STREAM"] is False


def test_provider_failure_fails_closed_no_token_leak():
    r = ops.resolve_identity_from_env(_env(), transport_factory=_factory(raises=True))
    assert r["ready"] is False and r["reason"].startswith("provider_error:")
    assert _TOKEN not in json.dumps(r)                  # exception message embedded the token; result must not


# ---- secret boundary ----
def test_token_never_in_result_or_output():
    fac = _factory()
    r = ops.resolve_identity_from_env(_env(), transport_factory=fac)
    assert _TOKEN not in json.dumps(r)
    assert fac.holder["t"].token == _TOKEN              # the token WAS used to build the transport ...
    buf = io.StringIO()
    code = ops.main(env=_env(), transport_factory=fac, out=buf)
    printed = buf.getvalue()
    assert code == 0 and _TOKEN not in printed          # ... but never printed


def test_main_exit_codes():
    ready = io.StringIO()
    assert ops.main(env=_env(), transport_factory=_factory(server_id="server-A"), out=ready) == 0
    notready = io.StringIO()
    assert ops.main(env=_env(), transport_factory=_factory(server_id="server-Z"), out=notready) == 1
    bad = _env()
    del bad[ops.ENV_SERVER_TOKEN]
    cfg = io.StringIO()
    assert ops.main(env=bad, transport_factory=_factory(), out=cfg) == 2
    out = json.loads(cfg.getvalue())
    assert out["ready"] is False and _TOKEN not in cfg.getvalue()


# ---- zero send / no DB ----
def test_zero_send_and_no_db_required():
    fac = _factory()
    ops.resolve_identity_from_env(_env(), transport_factory=fac)     # takes NO conn -> no canonical write
    assert fac.holder["t"].sends == 0                                # send_email never called
