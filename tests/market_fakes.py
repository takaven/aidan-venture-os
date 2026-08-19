"""Helpers + deterministic channel worker for the Gate 7 Slice 1 tests.

A channel worker is an ORDINARY Gate 4 ``WorkerAdapter`` (no DB, claim only). Setup
drives the real Gate-6 chain to OPERATING, then attaches a genuine Gate-3
validation_test (commercial hypothesis + precommitted max_spend) so market-action
freezing exercises the real kernel authority.
"""
from __future__ import annotations

from collections import namedtuple

from aidan_core.factory.workers import WorkerResult

from deploy_fakes import run_deploy

MarketSetup = namedtuple("MarketSetup", "venture_id opportunity_id validation_test_id")

DEFAULT_CONTENT = "Hi — quick question about how your clinic handles pre-auth follow-ups?"


class OutreachWorker:
    """A deterministic channel worker: records its request, returns a claim only."""

    kind = "outreach-a"

    def __init__(self, *, structured_output=None, suffix="1"):
        self._out = structured_output or {"status": "queued"}
        self._suffix = suffix
        self.calls = 0
        self.last_request = None

    def execute(self, request):  # no DB access — no market-truth authority
        self.calls += 1
        self.last_request = request
        return WorkerResult(
            worker_kind=self.kind, external_result_id=f"{self.kind}:{request.attempt_id}",
            reported_outcome="success", worker_version="test", structured_output=self._out)


class OutreachWorkerB(OutreachWorker):
    kind = "outreach-b"


class ChannelWorker:
    """A channel worker that MATERIALIZES the exact authorized outbound envelope + a
    deterministic acceptance into the controlled outbox. ``mode`` produces compliant or
    adversarial sends; the worker's self-report is inert (only the verifier decides)."""

    kind = "outreach-a"

    def __init__(self, *, mode="compliant", structured_output=None):
        self.mode = mode
        self._out = structured_output or {"status": "queued"}
        self.calls = 0
        self.last_request = None

    def execute(self, request):
        from aidan_core.market import channels as ch

        self.calls += 1
        self.last_request = request
        m = dict((request.task_payload or {}).get("market", {}))
        if self.mode != "nothing":
            content, audience = m["content"], m["audience_ref"]
            if self.mode == "wrong_content":
                content = content + " TAMPERED"
            if self.mode == "wrong_audience":
                audience = "aud://WRONG"
            env = {"content": content, "audience_ref": audience, "channel_kind": m["channel_kind"],
                   "source_instance_ref": m["source_instance_ref"], "action_spec_hash": m["action_spec_hash"],
                   "market_action_spec_id": m["market_action_spec_id"]}
            ch.write_envelope(m["outbox_path"], env, ch.acceptance_id(m["source_instance_ref"], request.attempt_id))
        return WorkerResult(
            worker_kind=self.kind, external_result_id=f"{self.kind}:{request.attempt_id}",
            reported_outcome="success", worker_version="test", structured_output=self._out)


class ChannelWorkerB(ChannelWorker):
    kind = "outreach-b"


MarketRun = namedtuple("MarketRun", "setup action_id spec worker verify")


def market_run(conn, slug, *, mode="compliant", key=None):
    """OPERATING venture -> freeze market_action_spec -> dispatch channel worker -> verify."""
    from aidan_core.market import runtime as market_runtime
    from factory_fakes import registry_with

    key = key or slug
    s = operating_setup(conn, slug, key=key)
    a = market_action(conn, s.venture_id, key=key)
    ms = freeze_outreach(conn, s, a)
    w = ChannelWorker(mode=mode)
    market_runtime.execute_market_action(conn, a, registry=registry_with(w), worker_kind=w.kind)
    out = market_runtime.verify_market_action(conn, a, actual_cost=0)
    return MarketRun(s, a, ms, w, out)


def operating_setup(conn, slug, *, key=None, max_spend="100") -> MarketSetup:
    """OPERATING venture (via the real Gate-6 chain) + a Gate-3 OUTREACH validation_test."""
    from aidan_core.deploy import state as deploy_state

    key = key or slug
    r = run_deploy(conn, slug, key=key)
    deploy_state.promote_verified_deployment(conn, r.s.deploy_action_id)  # -> OPERATING
    vid, opp = r.s.venture_id, r.s.eval.auth.opportunity_id
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, statement, "
            "statement_hash) VALUES (%s, %s, %s, %s, 'h') RETURNING id",
            (vid, f"vh-{key}", opp, "clinics are reachable via cold outreach"))
        vh = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, method, "
            "success_criterion, evidence_required, max_spend, definition_hash) "
            "VALUES (%s, %s, %s, 'OUTREACH', 'cold email', '>=5 qualified replies', 'reply logs', %s, 'h') RETURNING id",
            (vid, f"vt-{key}", vh, max_spend))
        vt = cur.fetchone()[0]
    return MarketSetup(vid, opp, vt)


def market_action(conn, venture_id, *, key, amount=0, required_autonomy=0):
    """Create a canonical send_outreach ActionRequest for a venture."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO action_request (venture_id, action_type, actor, payload, payload_hash, "
            "idempotency_key, required_autonomy, requested_amount, requested_currency) "
            "VALUES (%s, 'send_outreach', 'a', '{}'::jsonb, 'h', %s, %s, %s, 'USD') RETURNING id",
            (venture_id, f"mkt:{key}", required_autonomy, amount))
        return cur.fetchone()[0]


def freeze_outreach(conn, setup, action_id, **over):
    """Freeze a market_action_spec for an OUTREACH action, with field overrides."""
    from aidan_core.market import action as market_action_mod

    fields = dict(channel_kind="fake-local", audience_ref="aud://segment-1", content=DEFAULT_CONTENT,
                  offer_ref=None, price_amount=None, price_currency=None, offer_terms=None,
                  authorized_spend_amount=0, spend_currency="USD")
    fields.update(over)
    return market_action_mod.create_market_action_spec(
        conn, action_id, opportunity_id=setup.opportunity_id, validation_test_id=setup.validation_test_id, **fields)
