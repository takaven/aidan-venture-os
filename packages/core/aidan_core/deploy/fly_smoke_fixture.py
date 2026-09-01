"""Self-contained canonical fixture for the Stage-C Fly deploy smoke (Gate 6 real-deploy readiness).

The live smoke runs against a brand-new ephemeral PostgreSQL. It therefore establishes its OWN
bounded, deterministic, quality-qualified deploy fixture in THAT database — a foreign ActionRequest
id from another ephemeral run cannot exist here. Everything deploy-authority-relevant is built with
the real guarded kernel APIs (build_spec freeze, execute_build, quality assessment producing a
genuine Gate-5 PASS, deployment_target registration, canonical deploy ActionRequest,
create_release_candidate which re-checks quality from PostgreSQL). Only the upstream Gate-2/3
prerequisite chain (opportunity -> recommendation -> BUILD investment decision), which has no lighter
kernel API, is created with the same minimal canonical inserts the kernel's own build tests use — it
is prerequisite scaffolding, not a bypass of the deploy/quality authority the smoke proves.

The Fly app name + the exact digest-pinned public OCI image + runtime/health contract are inputs
(the app + image are owner-created external infrastructure); this module freezes them into the
immutable release_contract (hence release_hash) so the deploy authorizes EXACTLY that artifact.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from ..factory.workers import WorkerRegistry, WorkerResult
from . import artifact as artifact_mod

# Deterministic qualified-build fixture inputs (a valid Gate-5 PASS candidate; not the deploy target).
_TECH_CONTRACT = {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}
_CAPS = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]
_CANDIDATE = [{"path": "app/main.py", "content": "def run():\n    return 'fly-smoke'\n"}]
_PRODUCT_MANIFEST = {
    "buyer": "independent physiotherapy clinics",
    "workflows": ["calendar -> detect billable session -> draft pre-auth -> track approval"],
    "features": ["preauth_drafting", "approval_tracking"],
    "differentiators_implemented": ["payer-specific pre-auth rules engine", "calendar-native trigger"],
    "vocabulary": ["pre-auth", "payer", "clinic", "billable session"],
    "states": ["empty", "loading", "error"], "cta": ["submit_preauth"], "dead_ends": [],
}
_INTENT = dict(
    buyer="independent physiotherapy clinics",
    problem="manual insurance pre-authorization wastes hours per patient",
    value_proposition="auto-drafts and tracks pre-auth submissions from the clinic calendar",
    product_category="vertical clinic operations tool",
    primary_workflow="calendar -> detect billable session -> draft pre-auth -> track approval",
    differentiators=["payer-specific pre-auth rules engine", "calendar-native trigger"],
    required_capabilities=["preauth_drafting", "approval_tracking"],
    excluded_capabilities=["generic_crm", "generic_chatbot"],
    experience_principles=["one screen per pending pre-auth", "no empty dashboards"],
)

# Default Fly runtime/network contract for a plain HTTP image (e.g. nginx): the app is reachable at
# <app>.fly.dev only if the machine exposes an HTTP service. internal_port + image are owner inputs.
DEFAULT_PORTS = [{"port": 80, "handlers": ["http"]}, {"port": 443, "handlers": ["tls", "http"]}]


class _FixtureBuilder:
    """Deterministic build worker for the smoke fixture (claim only; no DB)."""

    kind = "fly-smoke-builder"

    def execute(self, request):
        return WorkerResult(
            worker_kind=self.kind, worker_version="1",
            external_result_id=f"{self.kind}:{request.action_request_id}",
            reported_outcome="success",
            structured_output={"status": "done", "candidate_files": _CANDIDATE,
                               "product_manifest": _PRODUCT_MANIFEST})


def _build_authority(conn, vid, *, key):
    """Minimal canonical Gate-2/3 prerequisite chain (opportunity -> recommendation -> BUILD
    decision -> build ActionRequest) — the same shape the kernel's build tests use, since there is
    no lighter kernel API for it. Returns (build_action_id, decision_id, recommendation_id, opp_id)."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO opportunity (venture_id, opportunity_key, buyer_hypothesis, "
                    "problem_hypothesis, payload_hash, status) VALUES (%s,%s,%s,%s,%s,'CANDIDATE') RETURNING id",
                    (vid, f"opp-{key}", "buyer-h", "problem-h", "h"))
        opp_id = cur.fetchone()[0]
        cur.execute("INSERT INTO next_action_recommendation (venture_id, recommendation_key, opportunity_id, "
                    "action_type, dominant_reason_code, input_hash) VALUES (%s,%s,%s,'BUILD','BUILD_CONSIDERATION_READY',%s) RETURNING id",
                    (vid, f"rec-{key}", opp_id, "h"))
        rec_id = cur.fetchone()[0]
        cur.execute("INSERT INTO action_request (venture_id, action_type, actor, payload, payload_hash, "
                    "idempotency_key, required_autonomy, requested_amount, requested_currency) "
                    "VALUES (%s,'build','a','{}'::jsonb,'h',%s,0,0,'USD') RETURNING id", (vid, f"commit:{key}"))
        build_aid = cur.fetchone()[0]
        cur.execute("INSERT INTO investment_decision_record (venture_id, decision, rationale_ref, "
                    "resulting_action_id, source_recommendation_id) VALUES (%s,'BUILD',%s,%s,%s) RETURNING id",
                    (vid, f"next_action_recommendation:{rec_id}", build_aid, rec_id))
        did = cur.fetchone()[0]
    return build_aid, did, rec_id, opp_id


def build_release_contract(*, image_ref, internal_port, health_path, health_marker=None,
                           region=None, ports=None) -> dict:
    """Freeze the Fly release_contract: exact digest-pinned image + expected_artifact_identity
    (digest extracted from the ref) + runtime/network contract + health contract."""
    digest = artifact_mod.normalize_digest(image_ref)
    rc = {
        "expected_artifact_identity": artifact_mod.build_expected_artifact_identity(digest),
        "image_ref": image_ref,
        "required_state": "started",
        "runtime_contract": {"internal_port": int(internal_port), "protocol": "tcp",
                             "ports": ports or DEFAULT_PORTS},
        "health_contract": {"path": health_path or "/", **({"marker_content": health_marker}
                                                           if health_marker else {})},
    }
    if region:
        rc["region"] = region
    return rc


def establish_fixture(conn, *, app, image_ref, internal_port, health_path="/", health_marker=None,
                      region=None, ports=None, grant=Decimal("1.00"), ceiling=Decimal("0.05"),
                      slug="gate8-fly-smoke", actor="fly-smoke"):
    """Establish the complete bounded canonical fixture and return the created IDs + release_contract.
    Deploy authority is fully guarded (create_release_candidate re-checks Gate-5 quality from DB)."""
    from .. import budget, execution, lifecycle, ventures
    from ..actions import submit_action_request
    from ..build import quality as quality_mod
    from ..build import repository as repo_mod
    from ..build import runtime as build_runtime
    from ..build import spec as build_spec
    from ..build import substrate as substrate_mod
    from . import release as release_mod
    from . import target as target_mod

    vid = ventures.create_venture(conn, slug=slug, autonomy_level=1)
    budget.grant_budget(conn, vid, amount=grant, currency="USD")
    build_aid, did, rec_id, opp_id = _build_authority(conn, vid, key=slug)
    build_spec.create_build_spec(
        conn, build_aid, source_investment_decision_id=did, source_recommendation_id=rec_id,
        opportunity_id=opp_id,
        expected_output_contract={"require": {"status": "done"}, "technical": _TECH_CONTRACT},
        **_INTENT)
    repo_mod.register_venture_repository(conn, vid, repository_ref=f"venture://{slug}/app")
    rel = substrate_mod.create_substrate_release(
        conn, release_key=f"rel-{slug}", source_sha="sha-fly", components=["CONFIG_BOUNDARY", "TEST_HARNESS"])
    reg = WorkerRegistry()
    reg.register(_FixtureBuilder())
    build_runtime.execute_build(conn, build_aid, registry=reg, worker_kind=_FixtureBuilder.kind,
                                verifier_kind="structured-contract", capability_scope=_CAPS,
                                timeout_seconds=60, max_attempts=1)
    cap = build_runtime.capture_and_check_build(conn, build_aid, substrate_release_id=rel.substrate_release_id)
    manifest_id = cap["manifest"].build_manifest_id
    build_runtime.assess_build_quality(conn, build_aid)
    if quality_mod.overall_verdict(conn, manifest_id) != "PASS":
        raise RuntimeError("fixture build did not reach Gate-5 quality PASS")

    target = target_mod.register_deployment_target(
        conn, vid, environment="staging", provider_kind="fly-machines", target_ref=app)
    deploy_aid = submit_action_request(
        conn, venture_id=vid, action_type="deploy", actor=actor, idempotency_key=f"deploy:{slug}",
        required_autonomy=0, requested_amount=ceiling, requested_currency="USD").action_id
    rc = build_release_contract(image_ref=image_ref, internal_port=internal_port,
                                health_path=health_path, health_marker=health_marker, region=region,
                                ports=ports)
    rel_res = release_mod.create_release_candidate(
        conn, deploy_aid, build_manifest_id=manifest_id, deployment_target_id=target.deployment_target_id,
        release_contract=rc)
    lifecycle.transition(conn, vid, "VALIDATING", actor=actor)
    lifecycle.transition(conn, vid, "BUILDING", actor=actor)
    return {
        "venture_id": str(vid), "build_manifest_id": str(manifest_id),
        "deployment_target_id": str(target.deployment_target_id), "deploy_action_id": str(deploy_aid),
        "release_candidate_id": str(rel_res.release_candidate_id), "release_hash": rel_res.release_hash,
        "release_contract": rc,
    }
