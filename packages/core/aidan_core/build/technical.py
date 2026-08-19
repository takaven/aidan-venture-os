"""Deterministic Technical build quality (Gate 5 Slice 2).

Technical quality is the ONLY Gate 5 dimension in Slice 2. It is derived from a
finite set of deterministic machine checks over the immutable build_manifest and
the venture workspace — never from a worker's or a model's self-report, and never
as a scalar score (any required check FAIL ⇒ Technical FAIL; one passing check
cannot compensate for another failing one).

Security boundary (Slice 2 Alpha): NO arbitrary Builder code is executed on the
host. The TEST_COMMAND check is an allowlist/structural check of the declared test
command, not sandboxed execution — real sandboxed execution is deferred. The only
real containment claim is that file materialization is root-contained to the
venture workspace (see ``workspace.py``).

Technical evidence is separate from the Gate 4 worker-execution proof: a worker
proof attests the bounded execution result, NOT product Technical quality. Slice 2
records Technical evidence only; it creates no proof, sets no lifecycle, and makes
no canonical BUILD success. Execution SUCCESS and Technical PASS are independent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from psycopg.types.json import Json

from .. import audit, db
from ..errors import IdempotencyConflictError, NotFoundError
from . import workspace as ws

# Finite allowlist of declarable test commands (structural check; not executed).
ALLOWED_TEST_COMMANDS = frozenset({"pytest", "python -m pytest", "npm test", "npm run test"})
# Bounded Technical-contract vocabulary carried under expected_output_contract["technical"].
TECHNICAL_CONTRACT_KEYS = frozenset({"required_files", "forbidden_files", "required_commands"})
# Bounded secret/credential file rules (Alpha).
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_SECRET_BASENAMES = frozenset({"id_rsa", "id_dsa", "credentials.json", ".npmrc", ".pgpass"})
_ENV_ALLOWED = frozenset({".env.example", ".env.template", ".env.sample"})

REQUIRED_CHECKS = (
    "CONTRACT_SCHEMA", "WORKSPACE_CONTAINMENT", "MANIFEST_INTEGRITY",
    "SUBSTRATE_PROVENANCE", "REQUIRED_OUTPUT", "FORBIDDEN_SECRET_FILE", "TEST_COMMAND",
)


@dataclass(frozen=True)
class TechnicalCheckResult:
    name: str
    result: str            # "PASS" | "FAIL"
    detail: dict


def is_forbidden_secret(path: str) -> bool:
    base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base in _SECRET_BASENAMES:
        return True
    if Path(base).suffix in _SECRET_SUFFIXES:
        return True
    if base == ".env":
        return True
    if base.startswith(".env.") and base not in _ENV_ALLOWED:
        return True
    return False


def _technical_block(expected_output_contract: dict) -> dict:
    return dict((expected_output_contract or {}).get("technical", {}))


# ---- individual deterministic checks -------------------------------------------------
def _check_contract_schema(tech) -> TechnicalCheckResult:
    unknown = sorted(set(tech) - TECHNICAL_CONTRACT_KEYS)
    return TechnicalCheckResult("CONTRACT_SCHEMA", "FAIL" if unknown else "PASS",
                                {"unknown_keys": unknown})


def _check_workspace_containment(entries, workspace_root) -> TechnicalCheckResult:
    escaped = []
    for e in entries:
        try:
            ws.contained_path(workspace_root, e["path"])
        except Exception:
            escaped.append(e["path"])
    return TechnicalCheckResult("WORKSPACE_CONTAINMENT", "FAIL" if escaped else "PASS",
                                {"escaped": escaped})


def _check_manifest_integrity(entries, workspace_root) -> TechnicalCheckResult:
    bad = []
    for e in entries:
        try:
            actual = ws.read_hash(workspace_root, e["path"])
        except Exception:
            bad.append({"path": e["path"], "reason": "missing"})
            continue
        if actual["sha256"] != e["sha256"] or actual["size"] != e["size"]:
            bad.append({"path": e["path"], "reason": "hash_mismatch"})
    return TechnicalCheckResult("MANIFEST_INTEGRITY", "FAIL" if bad else "PASS", {"bad": bad})


def _check_substrate_provenance(entries, component_manifest) -> TechnicalCheckResult:
    by_path = {e["path"]: e["sha256"] for e in entries}
    missing = []
    for c in component_manifest or []:
        if by_path.get(c["path"]) != c["sha256"]:
            missing.append(c["path"])
    return TechnicalCheckResult("SUBSTRATE_PROVENANCE", "FAIL" if missing else "PASS",
                                {"missing_or_mismatched": missing})


def _check_required_output(entries, tech) -> TechnicalCheckResult:
    paths = {e["path"] for e in entries}
    required = set(tech.get("required_files", []))
    forbidden = set(tech.get("forbidden_files", []))
    missing = sorted(required - paths)
    present_forbidden = sorted(forbidden & paths)
    ok = not missing and not present_forbidden
    return TechnicalCheckResult("REQUIRED_OUTPUT", "PASS" if ok else "FAIL",
                                {"missing_required": missing, "present_forbidden": present_forbidden})


def _check_forbidden_secret(entries) -> TechnicalCheckResult:
    hits = sorted(e["path"] for e in entries if is_forbidden_secret(e["path"]))
    return TechnicalCheckResult("FORBIDDEN_SECRET_FILE", "FAIL" if hits else "PASS", {"secrets": hits})


def _check_test_command(tech) -> TechnicalCheckResult:
    required = list(tech.get("required_commands", []))
    unknown = [c for c in required if c not in ALLOWED_TEST_COMMANDS]
    return TechnicalCheckResult("TEST_COMMAND", "FAIL" if unknown else "PASS",
                                {"disallowed_commands": unknown})


def evaluate_technical(entries, *, workspace_root, expected_output_contract, component_manifest) -> list:
    """Run all deterministic Technical checks; return ordered TechnicalCheckResults."""
    tech = _technical_block(expected_output_contract)
    return [
        _check_contract_schema(tech),
        _check_workspace_containment(entries, workspace_root),
        _check_manifest_integrity(entries, workspace_root),
        _check_substrate_provenance(entries, component_manifest),
        _check_required_output(entries, tech),
        _check_forbidden_secret(entries),
        _check_test_command(tech),
    ]


def _load_manifest_context(cur, build_manifest_id):
    cur.execute(
        "SELECT venture_id, build_spec_id, substrate_release_id, file_manifest "
        "FROM build_manifest WHERE id = %s", (build_manifest_id,))
    m = cur.fetchone()
    if m is None:
        raise NotFoundError(f"build_manifest {build_manifest_id} does not exist")
    venture_id, build_spec_id, substrate_release_id, file_manifest = m
    cur.execute("SELECT expected_output_contract FROM build_spec WHERE id = %s", (build_spec_id,))
    contract = (cur.fetchone() or [{}])[0] or {}
    cur.execute("SELECT component_manifest FROM substrate_release WHERE id = %s", (substrate_release_id,))
    component_manifest = (cur.fetchone() or [[]])[0] or []
    return venture_id, list(file_manifest or []), dict(contract), list(component_manifest)


def run_technical_checks(conn, build_manifest_id: str, workspace_root: str, *, actor: str = "factory") -> dict:
    """Evaluate + durably persist deterministic Technical evidence for a manifest.

    Evidence is immutable per (manifest, check): a re-run returns the first
    persisted observation rather than re-writing it. Returns the checks and the
    DERIVED verdict.
    """
    with conn.cursor() as cur:
        venture_id, entries, contract, component_manifest = _load_manifest_context(cur, build_manifest_id)
        cur.execute(
            "SELECT check_name, result FROM build_technical_check WHERE build_manifest_id = %s",
            (build_manifest_id,))
        already = {r[0]: r[1] for r in cur.fetchall()}

    results = evaluate_technical(
        entries, workspace_root=workspace_root, expected_output_contract=contract,
        component_manifest=component_manifest,
    )
    persisted = []
    for r in results:
        if r.name in already:
            persisted.append(TechnicalCheckResult(r.name, already[r.name], r.detail))
            continue
        with db.transaction(conn) as cur:
            cur.execute(
                "INSERT INTO build_technical_check (build_manifest_id, venture_id, check_name, result, detail) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (build_manifest_id, check_name) DO NOTHING",
                (build_manifest_id, venture_id, r.name, r.result, Json(r.detail)),
            )
        persisted.append(r)

    verdict = "PASS" if all(r.result == "PASS" for r in persisted) and persisted else "FAIL"
    with db.transaction(conn) as cur:
        audit.record_event(
            cur, event_type="build.technical_checks_recorded", actor=actor, venture_id=venture_id,
            action_id=None, payload={"build_manifest_id": str(build_manifest_id), "verdict": verdict},
        )
    return {"verdict": verdict, "checks": [{"name": r.name, "result": r.result} for r in persisted]}


def technical_verdict(conn, build_manifest_id: str) -> str:
    """Derive the Technical verdict from persisted evidence (no scalar score)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT result FROM build_technical_check WHERE build_manifest_id = %s", (build_manifest_id,))
        rows = [r[0] for r in cur.fetchall()]
    if not rows:
        return "PENDING"
    return "FAIL" if any(r == "FAIL" for r in rows) else "PASS"
