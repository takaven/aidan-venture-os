"""Bounded, MANUAL, fail-closed first live Codex coding-worker smoke.

Materializes EXACTLY the frozen preregistration, reserves a canonical USD 0.20 ceiling BEFORE
the provider can be dispatched, executes at most once, verifies behaviour in the trusted
Bubblewrap TEST_EXECUTION sandbox, reconciles cost under the merged conservative semantics,
and emits a single sanitized evidence line. It NEVER fabricates success and NEVER prints the
credential. The process boundary is an injectable seam, so the whole entrypoint is proven
deterministically with a fake provider — no real Codex is required for the tests.

Frozen identity (fail closed unless these match):
  harness SHA-256 : 74558063d95bb62b8c114a72e065a71271aad81bbca2b6e8e57f0af39bc75cad
  spec hash       : 421be6d1f703d8f96adc187838ead6561c107415d32ccbb6189a0f20e5552007
  model           : gpt-5-mini      CLI: codex-cli 0.151.0
  timeout 120s · max_attempts 1 · ceiling USD 0.20 · confirm RUN_FROZEN_CODEX_SMOKE
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from importlib import resources
from pathlib import Path

# ---- FROZEN preregistration constants ---------------------------------------
FROZEN_HARNESS_SHA256 = "74558063d95bb62b8c114a72e065a71271aad81bbca2b6e8e57f0af39bc75cad"
FROZEN_SPEC_HASH = "421be6d1f703d8f96adc187838ead6561c107415d32ccbb6189a0f20e5552007"
MODEL = "gpt-5-mini"
CODEX_CLI_VERSION = "codex-cli 0.151.0"
CONFIRM_TOKEN = "RUN_FROZEN_CODEX_SMOKE"
API_KEY_ENV = "WORKER_CODEX_API_KEY"
ARTIFACT_PATHS = ["candidate.py"]
TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 1
CEILING = Decimal("0.20")
CAPABILITIES = ["WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]
MIN_TESTS = 5

PROMPT = ("Create a file named candidate.py containing a single function "
          "slugify(text: str) -> str. It must return a URL-style slug of text: convert to "
          "lowercase, replace every maximal run of characters that are not ASCII letters or "
          "digits with a single hyphen \"-\", and strip any leading or trailing hyphens. Use "
          "only the Python standard library. Do not include tests or a __main__ block.")


class FrozenMismatch(Exception):
    """A frozen artifact (harness bytes or spec hash) does not match — never dispatch."""


def _load_harness() -> str:
    return resources.files("aidan_core.factory").joinpath("codex_smoke_harness.txt").read_text(encoding="utf-8")


def frozen_spec_inputs():
    """Return (task_payload, expected_output_contract, capability_scope) for the frozen smoke,
    after verifying the harness bytes and the computed spec hash against the frozen constants."""
    from . import spec as spec_mod
    from . import test_execution as te

    harness = _load_harness()
    sha = hashlib.sha256(harness.encode("utf-8")).hexdigest()
    if sha != FROZEN_HARNESS_SHA256:
        raise FrozenMismatch(f"harness sha256 {sha} != frozen {FROZEN_HARNESS_SHA256}")

    task_payload = {"prompt": PROMPT, "model": MODEL, "artifact_paths": list(ARTIFACT_PATHS)}
    contract = {"test_execution": {
        "harness_source": harness, "test_sha256": sha, "min_tests": MIN_TESTS,
        "runner_kind": te.RUNNER_KIND, "runner_version": te.RUNNER_VERSION,
        "timeout_seconds": TIMEOUT_SECONDS,
    }}
    spec_hash = spec_mod.compute_spec_hash(
        worker_kind="codex-exec", task_payload=task_payload, expected_output_contract=contract,
        verifier_kind="test-execution", timeout_seconds=TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS,
        capability_scope=CAPABILITIES)
    if spec_hash != FROZEN_SPEC_HASH:
        raise FrozenMismatch(f"spec hash {spec_hash} != frozen {FROZEN_SPEC_HASH}")
    return task_payload, contract, CAPABILITIES


def assert_codex_version(actual: str) -> None:
    if (actual or "").strip() != CODEX_CLI_VERSION:
        raise FrozenMismatch(f"codex version {actual!r} != frozen {CODEX_CLI_VERSION!r}")


def _make_git_workspace(base=None) -> str:
    d = tempfile.mkdtemp(prefix="codex-smoke-ws-", dir=base)
    subprocess.run(["git", "init", "-q", d], check=True)
    return d


def _captured_usage(conn, action_id):
    with conn.cursor() as cur:
        cur.execute("SELECT raw_payload FROM execution_result WHERE action_request_id = %s "
                    "ORDER BY received_at DESC LIMIT 1", (action_id,))
        row = cur.fetchone()
    if not row:
        return None
    return (row[0] or {}).get("structured_output", {}).get("token_usage")


def _budget_state(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount, granted_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = 'USD'", (vid,))
        return cur.fetchone()


def run_smoke(conn, *, transport=None, workspace_ref=None, actor="codex-smoke",
              grant=Decimal("1.00"), slug="gate8-codex-live-smoke"):
    """Execute the governed smoke against ``transport`` (real Codex when None). Returns a
    sanitized evidence dict. Provider dispatch is IMPOSSIBLE until the frozen checks pass and the
    canonical reservation exists; at most one provider invocation occurs (max_attempts=1)."""
    from decimal import Decimal as _D

    from .. import budget, ventures
    from ..actions import submit_action_request as _submit
    from ..errors import ExecutionBlockedError, InsufficientBudgetError
    from . import provider_cost, runtime, spec as spec_mod
    from .codex_worker import CodexExecWorker
    from .verifiers import default_registry
    from .workers import WorkerRegistry

    task_payload, contract, caps = frozen_spec_inputs()   # FrozenMismatch -> no dispatch

    ev = {"smoke": "gate8-codex", "repo_sha": os.environ.get("GITHUB_SHA", "local"),
          "spec_hash": None, "harness_sha256": FROZEN_HARNESS_SHA256, "worker_kind": "codex-exec",
          "codex_cli_version": CODEX_CLI_VERSION, "result": None, "provider_invocations": None}

    vid = ventures.create_venture(conn, slug=slug, autonomy_level=3)
    if grant:
        budget.grant_budget(conn, vid, amount=grant, currency="USD")
    aid = _submit(conn, venture_id=vid, action_type="spend", actor=actor, idempotency_key=slug,
                  required_autonomy=0, requested_amount=CEILING, requested_currency="USD").action_id
    spec = spec_mod.create_execution_spec(
        conn, aid, worker_kind="codex-exec", verifier_kind="test-execution",
        timeout_seconds=TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS, capability_scope=caps,
        task_payload=task_payload, expected_output_contract=contract, actor=actor)
    if spec.spec_hash != FROZEN_SPEC_HASH:
        raise FrozenMismatch(f"created spec hash {spec.spec_hash} != frozen")
    ev["spec_hash"] = spec.spec_hash

    worker = CodexExecWorker(transport=transport) if transport is not None else CodexExecWorker()
    reg = WorkerRegistry()
    reg.register(worker)
    workspace_ref = workspace_ref or _make_git_workspace()

    counter = {"n": 0}
    if transport is not None:
        _orig = transport
        def _counting(*a, **k):  # count real provider invocations for the <=1 invariant
            counter["n"] += 1
            return _orig(*a, **k)
        reg.get("codex-exec")._transport = _counting

    try:
        r = runtime.execute_action(conn, aid, registry=reg, workspace_ref=workspace_ref, actor=actor)
    except (InsufficientBudgetError, ExecutionBlockedError) as exc:
        # Blocked BEFORE any provider dispatch (e.g. the reservation cannot be made) -> the paid
        # provider is never reached. Report it; capital is untouched.
        blocked = "RESERVATION_FAILED" if "BUDGET" in str(exc).upper() else "BLOCKED_BEFORE_DISPATCH"
        ev.update(result=blocked, provider_invocations=0)
        return _finalize(conn, vid, aid, ev)

    ev["provider_invocations"] = counter["n"] if transport is not None else None
    ev["dispatch_status"] = r.action_status
    ev["failure_class"] = r.failure_class

    from .. import execution
    if execution.get_status(conn, aid) not in ("SUCCEEDED", "FAILED", "RECOVERY_REQUIRED"):
        # A result was captured -> verify + reconcile trusted cost derived from captured usage.
        usage = _captured_usage(conn, aid)
        cost, cost_class = provider_cost.estimate_cost(MODEL, usage, ceiling=CEILING)
        ev["cost_classification"] = cost_class
        out = runtime.verify_and_complete(conn, aid, verifier_registry=default_registry(), actual_cost=cost)
        ev["test_execution_verdict"] = "VERIFIED" if out.verified else "REJECTED"
    ev["result"] = "PASS" if execution.get_status(conn, aid) == "SUCCEEDED" else "FAIL"
    return _finalize(conn, vid, aid, ev)


def _finalize(conn, vid, aid, ev):
    from .. import execution
    b = _budget_state(conn, vid)
    if b is not None:
        ev["reserved"], ev["committed"], ev["granted"] = (str(b[0]), str(b[1]), str(b[2]))
    ev["final_status"] = execution.get_status(conn, aid)
    ev["action_request_id"] = str(aid)
    with conn.cursor() as cur:
        cur.execute("SELECT id, verification_type, result, evidence_hash FROM proof_receipt "
                    "WHERE action_request_id = %s ORDER BY created_at DESC LIMIT 1", (aid,))
        pr = cur.fetchone()
        # governance delta: research/factory smoke authors no investment/action/proof beyond its own
        cur.execute("SELECT count(*) FROM investment_decision_record WHERE venture_id = %s", (vid,))
        gov = cur.fetchone()[0]
    if pr is not None:
        ev["proof_receipt_id"], ev["proof_verification_type"] = str(pr[0]), pr[1]
        ev["proof_result"], ev["evidence_hash"] = pr[2], pr[3]
    ev["governance_deltas"] = gov
    # secret-leak self check (values read in-process, only tested for absence)
    key = os.environ.get(API_KEY_ENV)
    ev["secret_leak_check"] = "FAIL" if (key and key in json.dumps(ev)) else "PASS"
    return ev


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print(json.dumps({"smoke": "gate8-codex", "result": "CONFIG_ERROR", "reason": "DATABASE_URL"}))
        return 2
    if os.environ.get("CONFIRM", "") != CONFIRM_TOKEN:
        print(json.dumps({"smoke": "gate8-codex", "result": "CONFIRM_REQUIRED"}))
        return 2
    if not os.environ.get(API_KEY_ENV):
        print(json.dumps({"smoke": "gate8-codex", "result": "CONFIG_ERROR", "reason": API_KEY_ENV}))
        return 2
    try:
        out = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30)
        assert_codex_version((out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else "")
    except FileNotFoundError:
        print(json.dumps({"smoke": "gate8-codex", "result": "CONFIG_ERROR", "reason": "codex-cli-missing"}))
        return 2
    except FrozenMismatch as exc:
        print(json.dumps({"smoke": "gate8-codex", "result": "FROZEN_MISMATCH", "reason": str(exc)}))
        return 3

    import psycopg
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        ev = run_smoke(conn)   # real Codex transport
    except FrozenMismatch as exc:
        print(json.dumps({"smoke": "gate8-codex", "result": "FROZEN_MISMATCH", "reason": str(exc)}))
        return 3
    except Exception as exc:  # sanitized last-resort; never a raw traceback
        print(json.dumps({"smoke": "gate8-codex", "result": "UNEXPECTED_ERROR", "error_type": type(exc).__name__}))
        return 5
    finally:
        conn.close()
    print(json.dumps(ev, sort_keys=True))
    ok = (ev.get("result") == "PASS" and ev.get("secret_leak_check") == "PASS"
          and ev.get("governance_deltas") == 0 and (ev.get("provider_invocations") or 0) <= 1)
    sys.stderr.write(f"gate8 codex smoke: {ev.get('result')} verdict={ev.get('test_execution_verdict')} "
                     f"committed={ev.get('committed')} invocations={ev.get('provider_invocations')}\n")
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
