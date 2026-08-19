"""Helpers + deterministic deploy workers for the Gate 6 Slice 1 tests.

A deploy worker is an ORDINARY Gate 4 ``WorkerAdapter`` (no DB access, claim only).
Setup drives the real Gate-5 chain (via ``full_eval``) to obtain a quality-qualified
build_manifest, registers a venture-owned deployment target, and creates a genuine
``deploy`` ActionRequest — so release/authority tests exercise the real kernel.
"""
from __future__ import annotations

from collections import namedtuple

from aidan_core.factory.workers import WorkerResult

from build_fakes import GOOD_PRODUCT_MANIFEST, full_eval

DeploySetup = namedtuple("DeploySetup", "venture_id build_manifest_id target_id deploy_action_id eval")


class DeployWorker:
    """Deterministic deploy worker: records its request, returns a claim only."""

    kind = "deploy-a"

    def __init__(self, *, reported_outcome="success", structured_output=None, suffix="1"):
        self._outcome = reported_outcome
        self._out = structured_output or {}
        self._suffix = suffix
        self.calls = 0
        self.last_request = None

    def execute(self, request):  # no connection parameter — no DB authority
        self.calls += 1
        self.last_request = request
        return WorkerResult(
            worker_kind=self.kind,
            external_result_id=f"{self.kind}:{request.action_request_id}:{self._suffix}",
            reported_outcome=self._outcome, worker_version="test", structured_output=self._out,
        )


class DeployWorkerB(DeployWorker):
    kind = "deploy-b"


def deploy_action(conn, venture_id, *, key, amount=0, required_autonomy=0):
    """Create a genuine canonical ``deploy`` ActionRequest for a venture."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO action_request (venture_id, action_type, actor, payload, payload_hash, "
            "idempotency_key, required_autonomy, requested_amount, requested_currency) "
            "VALUES (%s, 'deploy', 'a', '{}'::jsonb, 'h', %s, %s, %s, 'USD') RETURNING id",
            (venture_id, f"deploy:{key}", required_autonomy, amount),
        )
        return cur.fetchone()[0]


def setup_deploy(conn, slug, *, key=None, product_manifest=None, environment="staging",
                 provider_kind="fake-a", target_ref=None):
    """Full Gate-5 chain + deployment target + deploy ActionRequest (no release yet)."""
    from aidan_core.deploy import target as target_mod

    key = key or slug
    r = full_eval(conn, slug, key=key, product_manifest=product_manifest or GOOD_PRODUCT_MANIFEST)
    target = target_mod.register_deployment_target(
        conn, r.auth.venture_id, environment=environment, provider_kind=provider_kind,
        target_ref=target_ref or f"deploy://{slug}/{environment}",
    )
    aid = deploy_action(conn, r.auth.venture_id, key=key)
    return DeploySetup(r.auth.venture_id, r.manifest_id, target.deployment_target_id, aid, r)
