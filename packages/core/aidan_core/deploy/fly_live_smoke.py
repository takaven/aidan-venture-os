"""Bounded, MANUAL, fail-closed first REAL_EXTERNAL Fly deployment BOUNDARY smoke (Stage C).

Establishes its OWN complete bounded canonical fixture (venture -> quality-qualified build/release ->
venture-owned Fly deployment_target -> deploy ActionRequest -> frozen release_candidate) in its own
ephemeral PostgreSQL, deploys the EXACT frozen OCI artifact the release authorizes to a venture-owned
Fly app, reads the running identity back INDEPENDENTLY, verifies deterministically, records a
DEPLOYMENT_RELEASE Proof Receipt, then DELETES the created machine and confirms its absence. It emits
ONE sanitized evidence line, never prints the credential, and never blind-retries an ambiguous
effect. Transport + health probe are injectable seams — the whole entrypoint is proven with fakes.

Stage-C scope. This PROVES: one real machine create, exact frozen OCI artifact authorization,
provider contact, deployment effect, independent digest read-back, bounded health, a
DEPLOYMENT_RELEASE proof, and deterministic cleanup. It DOES NOT prove: candidate_tree_hash -> OCI
build derivation (SOURCE_TO_ARTIFACT_DERIVATION_PROVEN is False), durable production operation, or
BUILDING -> OPERATING. The venture DELIBERATELY REMAINS BUILDING; the proof is historical evidence
the external deployment boundary worked, not a claim that a runtime remains operating.

Frozen bounds: provider fly-machines · ceiling USD 0.05 · max_attempts 1 · required state started ·
confirm RUN_FROZEN_FLY_DEPLOY_SMOKE · credential env DEPLOY_FLY_API_TOKEN.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal

PROVIDER_KIND = "fly-machines"
CEILING = Decimal("0.05")
CONFIRM_TOKEN = "RUN_FROZEN_FLY_DEPLOY_SMOKE"
TOKEN_ENV = "DEPLOY_FLY_API_TOKEN"
REQUIRED_STATE = "started"
TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 1
# Owner-provided external-target inputs for the manual run (the app + image are owner-created infra).
APP_ENV = "FLY_SMOKE_APP"
IMAGE_ENV = "FLY_SMOKE_IMAGE_REF"          # a digest-pinned public OCI image, e.g. nginx@sha256:...
PORT_ENV = "FLY_SMOKE_INTERNAL_PORT"       # the image's internal HTTP port (e.g. 80)
HEALTH_PATH_ENV = "FLY_SMOKE_HEALTH_PATH"
HEALTH_MARKER_ENV = "FLY_SMOKE_HEALTH_MARKER"
REGION_ENV = "FLY_SMOKE_REGION"
# Accepted-main SHA fail-close (mirrors the Codex smoke discipline): the workflow pins it.
ACCEPTED_SHA_ENV = "FLY_SMOKE_ACCEPTED_SHA"


def _captured_claim(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT raw_payload FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC LIMIT 1", (action_id,))
        row = cur.fetchone()
    return ((row[0] if row else {}) or {}).get("structured_output", {}) if row else {}


def _gov_count(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (vid,))
        return cur.fetchone()[0]


def _budget_state(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount, granted_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = 'USD'", (vid,))
        return cur.fetchone()


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        row = cur.fetchone()
    return row[0] if row else None


def default_health_probe(app, path, expected_marker):
    """Real external health probe: GET https://<app>.fly.dev/<path>, return the bounded marker text
    on HTTP 200, else None. Read-only; never mutates."""
    def _probe():
        import urllib.request
        url = f"https://{app}.fly.dev/{str(path or '').lstrip('/')}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:   # noqa: S310 (fixed https host)
                if resp.status != 200:
                    return None
                body = resp.read(4096).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            return None
        return body if body else "ok"
    return _probe


def run_fly_deploy_smoke(conn, *, app, image_ref, internal_port, health_path="/", health_marker=None,
                         region=None, transport=None, health_probe=None, actor="fly-smoke",
                         actual_cost=CEILING, slug="gate8-fly-smoke"):
    """Establish the fixture, govern one deploy, verify independently, record the proof, then clean up.
    Returns a sanitized evidence dict. Does NOT promote lifecycle (Stage C)."""
    from .. import execution
    from ..errors import ExecutionBlockedError, InsufficientBudgetError
    from . import fly_smoke_fixture, runtime as deploy_runtime
    from .fly_worker import FlyMachinesWorker, cleanup_machine
    from ..factory.workers import WorkerRegistry

    ev = {"smoke": "gate8-fly-deploy", "provider": PROVIDER_KIND, "repo_sha": os.environ.get("GITHUB_SHA", "local"),
          "ceiling": str(CEILING), "result": None, "deployment_effect": "NOT_OBSERVED",
          "provider_contact_evidence": "UNKNOWN", "cleanup_state": "NOT_ATTEMPTED",
          "source_to_artifact_derivation_proven": False, "promoted_to_operating": False}

    # 1. FIXTURE FREEZE — self-contained; the app/image are frozen into release_hash.
    fx = fly_smoke_fixture.establish_fixture(
        conn, app=app, image_ref=image_ref, internal_port=internal_port, health_path=health_path,
        health_marker=health_marker, region=region, ceiling=CEILING, slug=slug, actor=actor)
    vid = fx["venture_id"]
    deploy_action_id = fx["deploy_action_id"]
    ev["canonical_ids"] = {k: fx[k] for k in ("venture_id", "build_manifest_id", "deployment_target_id",
                                              "deploy_action_id", "release_candidate_id", "release_hash")}
    ev["action_request_id"] = deploy_action_id
    gov_baseline = _gov_count(conn, vid)   # baseline AFTER the (legitimate) BUILD decision

    worker = FlyMachinesWorker(transport=transport) if transport is not None else FlyMachinesWorker()
    reg = WorkerRegistry()
    reg.register(worker)

    # 2. GOVERNED MACHINE CREATE
    try:
        _dispatch, r = deploy_runtime.execute_deploy(
            conn, deploy_action_id, registry=reg, worker_kind=PROVIDER_KIND,
            timeout_seconds=TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS, actor=actor)
    except (InsufficientBudgetError, ExecutionBlockedError) as exc:
        blocked = "RESERVATION_FAILED" if "BUDGET" in str(exc).upper() else "BLOCKED_BEFORE_DISPATCH"
        ev["result"] = blocked
        return _finalize(conn, deploy_action_id, vid, ev, gov_baseline=gov_baseline)

    ev["dispatch_status"] = r.action_status
    ev["failure_class"] = r.failure_class
    status = execution.get_status(conn, deploy_action_id)
    if status == "RECOVERY_REQUIRED":
        ev.update(result="RECOVERY_REQUIRED", deployment_effect="UNKNOWN")
        return _finalize(conn, deploy_action_id, vid, ev, gov_baseline=gov_baseline)
    if status == "FAILED":
        ev.update(result="FAIL", deployment_effect="NOT_OBSERVED")
        return _finalize(conn, deploy_action_id, vid, ev, gov_baseline=gov_baseline)

    # 3. INDEPENDENT OBSERVER + 4. DETERMINISTIC VERIFICATION
    claim = _captured_claim(conn, deploy_action_id)
    machine_id = claim.get("machine_id")
    machine_app = claim.get("app") or app
    ev["machine_id"] = machine_id
    ev["deployment_effect"] = "OBSERVED" if machine_id else "UNKNOWN"

    token = os.environ.get(TOKEN_ENV)
    hp = health_probe or default_health_probe(machine_app, health_path, health_marker)
    obs_transport = transport if transport is not None else __import__(
        "aidan_core.deploy.fly_transport", fromlist=["HttpFlyTransport"]).HttpFlyTransport()

    def _observer_factory(contract):
        from .observe import FlyDeploymentObserver
        return FlyDeploymentObserver(
            fly_transport=obs_transport, token=token, app=contract.get("target_ref") or machine_app,
            machine_id=machine_id, venture_id=contract.get("venture_id"),
            deployment_target_id=contract.get("deployment_target_id"),
            health_probe=hp, required_state=REQUIRED_STATE, timeout=30.0)

    out = deploy_runtime.verify_deploy(conn, deploy_action_id, actual_cost=actual_cost, actor=actor,
                                       observer_factory=_observer_factory)
    ev["deployment_verdict"] = "VERIFIED" if out.verified else "REJECTED"
    ev["provider_contact_evidence"] = "OBSERVED" if machine_id else "UNKNOWN"

    # 5. GOVERNED CLEANUP of exactly the created machine (Stage C is ephemeral). NO promotion.
    if machine_id:
        ev["cleanup_state"] = cleanup_machine(obs_transport, token, machine_app, machine_id, timeout=30.0)

    if not out.verified:
        ev["result"] = "FAIL"
    elif ev["cleanup_state"] == "CLEANUP_CONFIRMED":
        ev["result"] = "PASS"
    else:
        # Verified boundary but cleanup not confirmed -> not a clean PASS; owner must reconcile the
        # machine (no new machine is ever created here).
        ev["result"] = "PASS_UNCLEAN_CLEANUP"
        ev["owner_reconciliation"] = {"app": machine_app, "machine_id": machine_id,
                                      "action": "verify machine deletion in the Fly app"}
    return _finalize(conn, deploy_action_id, vid, ev, gov_baseline=gov_baseline)


def _finalize(conn, action_id, vid, ev, *, gov_baseline=0):
    from .. import execution
    b = _budget_state(conn, vid)
    if b is not None:
        reserved, committed, granted = b
        ev["reserved"], ev["committed"], ev["granted"] = (str(reserved), str(committed), str(granted))
        ev["released"] = str(CEILING - committed)   # of the frozen ceiling, what was returned
    ev["lifecycle_state"] = _lifecycle(conn, vid)   # MUST stay BUILDING (Stage C never promotes)
    ev["governance_deltas"] = _gov_count(conn, vid) - gov_baseline
    with conn.cursor() as cur:
        cur.execute("SELECT id, verification_type, result, evidence_hash FROM proof_receipt "
                    "WHERE action_request_id = %s ORDER BY created_at DESC LIMIT 1", (action_id,))
        pr = cur.fetchone()
    if pr is not None:
        ev["proof_receipt_id"], ev["proof_verification_type"] = str(pr[0]), pr[1]
        ev["proof_result"], ev["evidence_hash"] = pr[2], pr[3]
    ev["final_status"] = execution.get_status(conn, action_id)
    token = os.environ.get(TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    # actual Fly billing is UNKNOWN unless independently observed; never claimed from accounting.
    ev["actual_provider_billing"] = "UNKNOWN"
    return ev


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIG_ERROR", "reason": "DATABASE_URL"}))
        return 2
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIRM_REQUIRED"}))
        return 2
    accepted = os.environ.get(ACCEPTED_SHA_ENV)
    if accepted and os.environ.get("GITHUB_SHA") and accepted != os.environ["GITHUB_SHA"]:
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "SHA_MISMATCH"}))
        return 3
    for env in (TOKEN_ENV, APP_ENV, IMAGE_ENV, PORT_ENV):
        if not os.environ.get(env):
            print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIG_ERROR", "reason": env}))
            return 2

    import psycopg
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        ev = run_fly_deploy_smoke(
            conn, app=os.environ[APP_ENV], image_ref=os.environ[IMAGE_ENV],
            internal_port=int(os.environ[PORT_ENV]), health_path=os.environ.get(HEALTH_PATH_ENV, "/"),
            health_marker=os.environ.get(HEALTH_MARKER_ENV), region=os.environ.get(REGION_ENV))
    except Exception as exc:  # sanitized last-resort; never a raw traceback / provider body
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    finally:
        conn.close()
    print(json.dumps(ev, sort_keys=True))
    ok = (ev.get("result") == "PASS" and ev.get("secret_leak_check") == "PASS"
          and ev.get("governance_deltas") == 0 and ev.get("lifecycle_state") == "BUILDING")
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
