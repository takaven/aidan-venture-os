"""Bounded, MANUAL, fail-closed first real Fly deployment smoke (Gate 6 real-deploy readiness).

Deploys an ALREADY quality-qualified, frozen ``release_candidate`` (whose immutable
``expected_artifact_identity`` = an OCI image digest) to a venture-owned Fly app through the
governed Gate-4/6 path, reads the running identity back INDEPENDENTLY through a Fly observer,
promotes ONLY from a VERIFIED ``DEPLOYMENT_RELEASE`` proof, and emits ONE sanitized evidence line.
It never fabricates success, never prints the credential, and never blind-retries an ambiguous
effect. The Fly transport + health probe are injectable seams, so the whole entrypoint is proven
deterministically with fakes — no real Fly is required for the tests.

Frozen smoke bounds: provider ``fly-machines`` · ceiling USD 0.05 · max_attempts 1 · required state
``started`` · confirm ``RUN_FROZEN_FLY_DEPLOY_SMOKE`` · credential env ``DEPLOY_FLY_API_TOKEN``.

Establishing the qualified release (Gate 5 build + quality) and the venture-owned Fly app is an
owner/preceding-gate responsibility; this entrypoint governs only the deploy + verify + promote.
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
# Env the owner sets for the manual run: the deploy ActionRequest whose frozen release_candidate
# (with expected_artifact_identity) is to be deployed, and the app's external health path.
ACTION_ENV = "FLY_SMOKE_DEPLOY_ACTION_ID"
HEALTH_PATH_ENV = "FLY_SMOKE_HEALTH_PATH"
HEALTH_MARKER_ENV = "FLY_SMOKE_HEALTH_MARKER"


def _captured_claim(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT raw_payload FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC LIMIT 1", (action_id,))
        row = cur.fetchone()
    return ((row[0] if row else {}) or {}).get("structured_output", {}) if row else {}


def _gov_count(conn, action_id):
    """Count investment_decision_record for the action's venture (the governance-delta baseline)."""
    with conn.cursor() as cur:
        cur.execute("SELECT venture_id FROM action_request WHERE id = %s", (action_id,))
        row = cur.fetchone()
        if row is None:
            return 0
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (row[0],))
        return cur.fetchone()[0]


def _budget_state(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount, granted_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = 'USD'", (vid,))
        return cur.fetchone()


def default_health_probe(app, path, expected_marker, *, http=None):
    """Real external health probe: GET https://<app>.fly.dev/<path>, return the bounded marker text
    on HTTP 200 (optionally matched), else None. Read-only; never mutates."""
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


def run_fly_deploy_smoke(conn, deploy_action_id, *, transport=None, health_probe=None,
                         actor="fly-smoke", actual_cost=CEILING):
    """Govern the deploy of a frozen release to Fly and independently verify it. Returns a sanitized
    evidence dict. Provider dispatch is impossible until the frozen release + expected digest exist
    (prepare guard); at most one create occurs (max_attempts=1); success comes only from the verifier."""
    from .. import execution
    from ..errors import ExecutionBlockedError, InsufficientBudgetError
    from . import runtime as deploy_runtime
    from . import state as deploy_state
    from .fly_worker import FlyMachinesWorker
    from ..factory.workers import WorkerRegistry

    ev = {"smoke": "gate8-fly-deploy", "provider": PROVIDER_KIND, "repo_sha": os.environ.get("GITHUB_SHA", "local"),
          "ceiling": str(CEILING), "result": None, "deployment_effect": "NOT_OBSERVED",
          "provider_contact_evidence": "UNKNOWN", "action_request_id": str(deploy_action_id)}

    # Governance-delta baseline: the venture is already quality-qualified (Gate 5), so it legitimately
    # carries prior investment decisions. The deploy must author ZERO new ones — measure the DELTA.
    gov_baseline = _gov_count(conn, deploy_action_id)

    worker = FlyMachinesWorker(transport=transport) if transport is not None else FlyMachinesWorker()
    reg = WorkerRegistry()
    reg.register(worker)

    try:
        _dispatch, r = deploy_runtime.execute_deploy(
            conn, deploy_action_id, registry=reg, worker_kind=PROVIDER_KIND,
            timeout_seconds=TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS, actor=actor)
    except (InsufficientBudgetError, ExecutionBlockedError) as exc:
        blocked = "RESERVATION_FAILED" if "BUDGET" in str(exc).upper() else "BLOCKED_BEFORE_DISPATCH"
        ev.update(result=blocked)
        return _finalize(conn, deploy_action_id, ev, gov_baseline=gov_baseline)

    ev["dispatch_status"] = r.action_status
    ev["failure_class"] = r.failure_class
    status = execution.get_status(conn, deploy_action_id)
    if status == "RECOVERY_REQUIRED":
        ev.update(result="RECOVERY_REQUIRED", deployment_effect="UNKNOWN")
        return _finalize(conn, deploy_action_id, ev, gov_baseline=gov_baseline)
    if status == "FAILED":
        # A definitive no-effect failure or provider rejection (never dispatched a machine we kept).
        ev.update(result="FAIL", deployment_effect="NOT_OBSERVED")
        return _finalize(conn, deploy_action_id, ev, gov_baseline=gov_baseline)

    # A machine claim was captured -> read the durable identity and verify it INDEPENDENTLY.
    claim = _captured_claim(conn, deploy_action_id)
    machine_id = claim.get("machine_id")
    app = claim.get("app")
    ev["machine_id"] = machine_id
    ev["deployment_effect"] = "OBSERVED" if machine_id else "UNKNOWN"

    token = os.environ.get(TOKEN_ENV)
    hp = health_probe
    if hp is None:
        hp = default_health_probe(app, os.environ.get(HEALTH_PATH_ENV, "healthz"),
                                  os.environ.get(HEALTH_MARKER_ENV))
    obs_transport = transport if transport is not None else __import__(
        "aidan_core.deploy.fly_transport", fromlist=["HttpFlyTransport"]).HttpFlyTransport()

    def _observer_factory(contract):
        from .observe import FlyDeploymentObserver
        return FlyDeploymentObserver(
            fly_transport=obs_transport, token=token, app=contract.get("target_ref") or app,
            machine_id=machine_id, venture_id=contract.get("venture_id"),
            deployment_target_id=contract.get("deployment_target_id"),
            health_probe=hp, required_state=REQUIRED_STATE, timeout=30.0)

    out = deploy_runtime.verify_deploy(conn, deploy_action_id, actual_cost=actual_cost, actor=actor,
                                       observer_factory=_observer_factory)
    ev["deployment_verdict"] = "VERIFIED" if out.verified else "REJECTED"
    ev["provider_contact_evidence"] = "OBSERVED" if machine_id else "UNKNOWN"
    if out.verified:
        promo = deploy_state.promote_verified_deployment(conn, deploy_action_id, actor=actor)
        ev["promotion"] = promo.get("outcome")
    ev["result"] = "PASS" if execution.get_status(conn, deploy_action_id) == "SUCCEEDED" else "FAIL"
    return _finalize(conn, deploy_action_id, ev, gov_baseline=gov_baseline)


def _finalize(conn, action_id, ev, *, gov_baseline=0):
    from .. import execution
    with conn.cursor() as cur:
        cur.execute("SELECT venture_id FROM action_request WHERE id = %s", (action_id,))
        row = cur.fetchone()
    vid = row[0] if row else None
    if vid is not None:
        b = _budget_state(conn, vid)
        if b is not None:
            ev["reserved"], ev["committed"], ev["granted"] = (str(b[0]), str(b[1]), str(b[2]))
        with conn.cursor() as cur:
            cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
            lr = cur.fetchone()
            ev["lifecycle_state"] = lr[0] if lr else None
            cur.execute("SELECT id, verification_type, result, evidence_hash FROM proof_receipt "
                        "WHERE action_request_id = %s ORDER BY created_at DESC LIMIT 1", (action_id,))
            pr = cur.fetchone()
            cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (vid,))
            # DELTA vs the pre-deploy baseline: a deploy must author no new investment decision.
            ev["governance_deltas"] = cur.fetchone()[0] - gov_baseline
        if pr is not None:
            ev["proof_receipt_id"], ev["proof_verification_type"] = str(pr[0]), pr[1]
            ev["proof_result"], ev["evidence_hash"] = pr[2], pr[3]
    ev["final_status"] = execution.get_status(conn, action_id)
    # secret-leak self check (token read in-process, only tested for ABSENCE)
    token = os.environ.get(TOKEN_ENV)
    ev["secret_leak_check"] = "FAIL" if (token and token in json.dumps(ev)) else "PASS"
    return ev


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIG_ERROR", "reason": "DATABASE_URL"}))
        return 2
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIRM_REQUIRED"}))
        return 2
    if not os.environ.get(TOKEN_ENV):
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIG_ERROR", "reason": TOKEN_ENV}))
        return 2
    action_id = os.environ.get(ACTION_ENV)
    if not action_id:
        # The qualified release + deploy ActionRequest must be established (owner/preceding gate).
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "CONFIG_ERROR", "reason": ACTION_ENV}))
        return 2

    import psycopg
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        ev = run_fly_deploy_smoke(conn, action_id)   # real Fly transport + health probe
    except Exception as exc:  # sanitized last-resort; never a raw traceback / provider body
        print(json.dumps({"smoke": "gate8-fly-deploy", "result": "UNEXPECTED_ERROR",
                          "error_type": type(exc).__name__}))
        return 5
    finally:
        conn.close()
    print(json.dumps(ev, sort_keys=True))
    ok = (ev.get("result") == "PASS" and ev.get("secret_leak_check") == "PASS"
          and ev.get("governance_deltas") == 0)
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
