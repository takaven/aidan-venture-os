"""Self-contained canonical fixture for the owner-controlled Postmark ingress smoke (Gate 8).

A real market send requires an OPERATING venture with a Gate-3 validation_test and a frozen
market_action_spec. The live smoke runs on a fresh ephemeral database, so it establishes that whole
governed chain itself via the real guarded kernel APIs: Gate-5 quality-qualified build -> local
Gate-6 deploy -> proof-gated promotion to OPERATING -> validation_test -> send_outreach ActionRequest
-> create_market_action_spec (which enforces OPERATING + validation-test spend bound + capital). Only
the recipient (owner-controlled) and provider identity are supplied by the owner; the subject/body/
channel/ceiling are frozen (postmark_smoke_spec). Everything authority-relevant is API-guarded; only
the upstream Gate-2/3 opportunity/recommendation/BUILD/validation prerequisites use minimal canonical
inserts (no lighter kernel API exists) — never a bypass of the market/deploy/quality authority.
"""
from __future__ import annotations

from decimal import Decimal

from ..factory.workers import WorkerRegistry, WorkerResult
# Reuse the vetted Gate-5 quality-passing build fixture (same package family, stable).
from ..deploy.fly_smoke_fixture import (_CAPS, _FixtureBuilder, _INTENT, _TECH_CONTRACT, _build_authority)
from . import postmark_smoke_spec as spec


class _LocalDeployWorker:
    """Materializes the release bundle + a health marker into the controlled LOCAL target so the
    deterministic deployment verifier can VERIFY and the venture can be promoted to OPERATING. Claim
    only; no DB. (The market smoke's deploy is local/controlled — the REAL external deploy boundary
    is proven separately by the Fly Stage-C smoke.)"""

    kind = "local-deploy"

    def execute(self, request):
        import shutil
        from pathlib import Path
        block = dict((request.task_payload or {}).get("deploy", {}))
        tp = Path(block["target_path"])
        shutil.rmtree(tp, ignore_errors=True)
        for f in block.get("deploy_bundle", []):
            dest = tp / "release" / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(str(f["content"]).encode("latin-1"))
        hf = tp / ".deploy" / "health"
        hf.parent.mkdir(parents=True, exist_ok=True)
        hf.write_bytes(b"ok")
        return WorkerResult(worker_kind=self.kind, worker_version="1",
                            external_result_id=f"{self.kind}:{request.attempt_id}",
                            reported_outcome="success", structured_output={"deployed": True})


def _operating_venture(conn, *, slug, actor, grant):
    """Gate-5 build -> local Gate-6 deploy -> promote to OPERATING. Returns (venture_id, opportunity_id)."""
    from .. import budget, lifecycle, ventures
    from ..actions import submit_action_request
    from ..build import quality as quality_mod
    from ..build import repository as repo_mod
    from ..build import runtime as build_runtime
    from ..build import spec as build_spec
    from ..build import substrate as substrate_mod
    from ..deploy import release as release_mod
    from ..deploy import runtime as deploy_runtime
    from ..deploy import state as deploy_state
    from ..deploy import target as target_mod

    vid = ventures.create_venture(conn, slug=slug, autonomy_level=3)
    budget.grant_budget(conn, vid, amount=grant, currency="USD")
    build_aid, did, rec_id, opp_id = _build_authority(conn, vid, key=slug)
    build_spec.create_build_spec(
        conn, build_aid, source_investment_decision_id=did, source_recommendation_id=rec_id,
        opportunity_id=opp_id,
        expected_output_contract={"require": {"status": "done"}, "technical": _TECH_CONTRACT}, **_INTENT)
    repo_mod.register_venture_repository(conn, vid, repository_ref=f"venture://{slug}/app")
    rel = substrate_mod.create_substrate_release(
        conn, release_key=f"rel-{slug}", source_sha="sha-mkt", components=["CONFIG_BOUNDARY", "TEST_HARNESS"])
    breg = WorkerRegistry(); breg.register(_FixtureBuilder())
    build_runtime.execute_build(conn, build_aid, registry=breg, worker_kind=_FixtureBuilder.kind,
                                verifier_kind="structured-contract", capability_scope=_CAPS,
                                timeout_seconds=60, max_attempts=1)
    cap = build_runtime.capture_and_check_build(conn, build_aid, substrate_release_id=rel.substrate_release_id)
    manifest_id = cap["manifest"].build_manifest_id
    build_runtime.assess_build_quality(conn, build_aid)
    if quality_mod.overall_verdict(conn, manifest_id) != "PASS":
        raise RuntimeError("fixture build did not reach Gate-5 quality PASS")

    target = target_mod.register_deployment_target(
        conn, vid, environment="staging", provider_kind="local", target_ref=f"local://{slug}")
    deploy_aid = submit_action_request(
        conn, venture_id=vid, action_type="deploy", actor=actor, idempotency_key=f"deploy:{slug}",
        required_autonomy=0, requested_amount=0, requested_currency="USD").action_id
    release_mod.create_release_candidate(conn, deploy_aid, build_manifest_id=manifest_id,
                                         deployment_target_id=target.deployment_target_id)
    lifecycle.transition(conn, vid, "VALIDATING", actor=actor)
    lifecycle.transition(conn, vid, "BUILDING", actor=actor)
    dreg = WorkerRegistry(); dreg.register(_LocalDeployWorker())
    deploy_runtime.execute_deploy(conn, deploy_aid, registry=dreg, worker_kind=_LocalDeployWorker.kind,
                                  timeout_seconds=60, max_attempts=1, actor=actor)
    out = deploy_runtime.verify_deploy(conn, deploy_aid, actual_cost=0, actor=actor)
    if not out.verified:
        raise RuntimeError("fixture local deploy did not VERIFY")
    deploy_state.promote_verified_deployment(conn, deploy_aid, actor=actor)   # -> OPERATING
    return vid, opp_id


def _validation_test(conn, vid, opp_id, *, key, max_spend):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO validation_hypothesis (venture_id, hypothesis_key, opportunity_id, "
                    "statement, statement_hash) VALUES (%s,%s,%s,%s,'h') RETURNING id",
                    (vid, f"vh-{key}", opp_id, "owner-controlled recipient is reachable"))
        vh = cur.fetchone()[0]
        cur.execute("INSERT INTO validation_test (venture_id, test_key, validation_hypothesis_id, test_type, "
                    "method, success_criterion, evidence_required, max_spend, definition_hash) "
                    "VALUES (%s,%s,%s,'OUTREACH','one owner send','delivered to owner','provider record',%s,'h') RETURNING id",
                    (vid, f"vt-{key}", vh, str(max_spend)))
        return cur.fetchone()[0]


def establish_postmark_smoke_action(conn, *, audience_ref="aud://owner", slug="gate8-postmark-smoke",
                                    actor="market-smoke", grant=Decimal("1.00")):
    """Establish the full governed chain and return the ids for a single owner-controlled send.
    The market_action_spec freezes the smoke subject/body/channel and binds spend to the ceiling."""
    from ..actions import submit_action_request
    from . import action as market_action_mod

    vid, opp_id = _operating_venture(conn, slug=slug, actor=actor, grant=grant)
    vt = _validation_test(conn, vid, opp_id, key=slug, max_spend="1.00")
    action_id = submit_action_request(
        conn, venture_id=vid, action_type="send_outreach", actor=actor, idempotency_key=f"mkt:{slug}",
        required_autonomy=0, requested_amount=spec.CEILING, requested_currency="USD").action_id
    market_action_mod.create_market_action_spec(
        conn, action_id, opportunity_id=opp_id, validation_test_id=vt,
        channel_kind=spec.CHANNEL, audience_ref=audience_ref, content=spec.SMOKE_BODY,
        authorized_spend_amount=spec.CEILING, spend_currency="USD", actor=actor)
    return {"venture_id": str(vid), "opportunity_id": str(opp_id), "validation_test_id": str(vt),
            "action_id": str(action_id)}
