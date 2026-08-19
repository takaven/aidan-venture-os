"""Helpers + deterministic deploy workers for the Gate 6 Slice 1 tests.

A deploy worker is an ORDINARY Gate 4 ``WorkerAdapter`` (no DB access, claim only).
Setup drives the real Gate-5 chain (via ``full_eval``) to obtain a quality-qualified
build_manifest, registers a venture-owned deployment target, and creates a genuine
``deploy`` ActionRequest — so release/authority tests exercise the real kernel.
"""
from __future__ import annotations

from collections import namedtuple

from aidan_core.factory.workers import WorkerResult

from build_fakes import (
    GOOD_PRODUCT_MANIFEST,
    BuilderWorker,
    build_authority_for,
    freeze_default_build_spec,
    full_eval,
    make_substrate,
)
from factory_fakes import registry_with

DeploySetup = namedtuple("DeploySetup", "venture_id build_manifest_id target_id deploy_action_id eval")

_CAPS2 = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]
_TECH = {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}


def second_qualified_manifest(conn, venture_id, *, key, candidate_files, product_manifest=None):
    """Build a SECOND, independently Gate-5-quality-qualified build_manifest in the SAME
    venture through the real governed chain. Reuses the venture's existing repository."""
    from aidan_core.build import runtime as build_runtime

    auth = build_authority_for(conn, venture_id, key=key)
    freeze_default_build_spec(conn, auth, expected_output_contract={
        "require": {"status": "done"}, "technical": _TECH})
    rel = make_substrate(conn, key=f"rel-{key}")
    worker = BuilderWorker(structured_output={
        "status": "done", "candidate_files": candidate_files,
        "product_manifest": product_manifest or GOOD_PRODUCT_MANIFEST})
    build_runtime.execute_build(
        conn, auth.action_id, registry=registry_with(worker), worker_kind=worker.kind,
        verifier_kind="structured-contract", capability_scope=_CAPS2, timeout_seconds=60, max_attempts=1)
    cap = build_runtime.capture_and_check_build(conn, auth.action_id, substrate_release_id=rel.substrate_release_id)
    build_runtime.assess_build_quality(conn, auth.action_id)
    return cap["manifest"].build_manifest_id


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


class DeployBundleWorker:
    """A deploy worker that MATERIALIZES the release bundle + health marker into the real
    venture-isolated target. ``mode`` produces compliant or adversarial deployments; the
    worker's self-report is inert (only the deterministic verifier decides)."""

    kind = "deploy-a"

    def __init__(self, *, mode="compliant", structured_output=None):
        self.mode = mode
        self._out = structured_output or {"deployed": True}
        self.calls = 0
        self.last_request = None

    def execute(self, request):  # no DB access
        import shutil
        from pathlib import Path

        self.calls += 1
        self.last_request = request
        block = dict((request.task_payload or {}).get("deploy", {}))
        tp = Path(block["target_path"])
        shutil.rmtree(tp, ignore_errors=True)  # deterministic fresh deploy
        if self.mode != "nothing":
            for f in block.get("deploy_bundle", []):
                content = f["content"]                       # lossless latin-1 of exact bytes
                if self.mode == "wrong_bytes" and f["path"].endswith("main.py"):
                    content = content + "TAMPERED"
                dest = tp / "release" / f["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content.encode("latin-1"))
            if self.mode not in ("no_health",):
                hf = tp / ".deploy" / "health"
                hf.parent.mkdir(parents=True, exist_ok=True)
                hf.write_bytes(b"ok")
        return WorkerResult(
            # attempt-scoped identity: a retry is a new attempt, so its external result
            # id must differ (Gate-4 forbids one external_result_id across attempts).
            worker_kind=self.kind, external_result_id=f"{self.kind}:{request.attempt_id}",
            reported_outcome="success", worker_version="test", structured_output=self._out)


class DeployBundleWorkerB(DeployBundleWorker):
    kind = "deploy-b"


def to_building(conn, venture_id):
    """Move a venture DISCOVERED -> VALIDATING -> BUILDING via the sole lifecycle authority."""
    from aidan_core import lifecycle
    lifecycle.transition(conn, venture_id, "VALIDATING", actor="op")
    lifecycle.transition(conn, venture_id, "BUILDING", actor="op")


DeployRun = namedtuple("DeployRun", "s worker verify")


def run_deploy(conn, slug, *, mode="compliant", release_contract=None, key=None, provider="fake-a",
               worker_cls=None, structured_output=None, max_attempts=1, building=True, environment="staging"):
    """End-to-end Gate-6 chain: release_candidate -> execute_deploy -> verify_deploy.
    Returns (setup, worker, verify_outcome). Promotion is left to the caller."""
    from aidan_core.deploy import release as release_mod
    from aidan_core.deploy import runtime as deploy_runtime

    s = setup_deploy(conn, slug, key=key or slug, provider_kind=provider, environment=environment,
                     target_ref=f"deploy://{slug}/{environment}")
    release_mod.create_release_candidate(
        conn, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
        deployment_target_id=s.target_id, release_contract=release_contract)
    if building:
        to_building(conn, s.venture_id)
    worker = (worker_cls or DeployBundleWorker)(mode=mode, structured_output=structured_output)
    deploy_runtime.execute_deploy(conn, s.deploy_action_id, registry=registry_with(worker),
                                  worker_kind=worker.kind, max_attempts=max_attempts)
    out = deploy_runtime.verify_deploy(conn, s.deploy_action_id, actual_cost=0)
    return DeployRun(s, worker, out)


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
