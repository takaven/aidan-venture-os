"""Deterministic deployment verification checks (Gate 6 Slice 2).

These run inside the trusted verifier boundary over a controlled LOCAL target
directory — no network, no provider account, no arbitrary code execution. They
independently establish observed deployment state; a worker's self-report is never
consulted. The controlled local target proves the architecture (release identity,
health, runtime contract, venture isolation), NOT live cloud deployment.

Target layout (per venture-isolated deployment target):
    <target_path>/release/...   the deployed candidate tree (must match release identity)
    <target_path>/.deploy/health   a bounded deterministic health marker

``release_tree_hash`` reproduces the exact formula ``build.manifest`` used for a
candidate tree, so the observed deployed tree hash can be compared to the frozen
``release_candidate`` identity.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..actions import canonical_payload_hash

REQUIRED_CHECKS = (
    "VENTURE_TARGET_ISOLATION", "TARGET_EXISTS", "RELEASE_IDENTITY",
    "HEALTH", "REQUIRED_RUNTIME_CONTRACT",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    result: str            # "PASS" | "FAIL"
    detail: dict


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def release_dir(target_path: str) -> Path:
    return Path(target_path) / "release"


def health_file(target_path: str) -> Path:
    return Path(target_path) / ".deploy" / "health"


def release_tree_hash(release_root: Path) -> Optional[str]:
    """Kernel-derived hash of the actual deployed tree (same formula as build.manifest)."""
    if not release_root.is_dir():
        return None
    entries = []
    for p in sorted(release_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(release_root).as_posix()
            entries.append({"path": rel, "sha256": _sha256(p.read_bytes())})
    entries.sort(key=lambda e: e["path"])
    return canonical_payload_hash({"files": entries})


# ---- individual deterministic checks -------------------------------------------------
def check_venture_isolation(target_path, venture_id, deployment_target_id) -> CheckResult:
    # The target path must resolve under this venture's + target's namespace.
    parts = Path(target_path).as_posix().split("/")
    ok = str(venture_id) in parts and str(deployment_target_id) in parts
    return CheckResult("VENTURE_TARGET_ISOLATION", "PASS" if ok else "FAIL",
                       {"venture_id": str(venture_id)})


def check_target_exists(target_path) -> CheckResult:
    root = release_dir(target_path)
    ok = root.is_dir() and any(p.is_file() for p in root.rglob("*"))
    return CheckResult("TARGET_EXISTS", "PASS" if ok else "FAIL", {"release_dir": str(root)})


def check_release_identity(target_path, expected_tree_hash) -> CheckResult:
    observed = release_tree_hash(release_dir(target_path))
    ok = observed is not None and observed == expected_tree_hash
    return CheckResult("RELEASE_IDENTITY", "PASS" if ok else "FAIL",
                       {"expected": expected_tree_hash, "observed": observed})


def check_health(target_path, health_contract) -> CheckResult:
    hf = health_file(target_path)
    if not hf.is_file():
        return CheckResult("HEALTH", "FAIL", {"reason": "no health marker"})
    expected = (health_contract or {}).get("marker_content")
    if expected is not None and hf.read_text(encoding="utf-8").strip() != str(expected):
        return CheckResult("HEALTH", "FAIL", {"reason": "marker content mismatch"})
    return CheckResult("HEALTH", "PASS", {})


def check_runtime_contract(target_path, release_contract) -> CheckResult:
    root = release_dir(target_path)
    missing = []
    entry = (release_contract or {}).get("entry_artifact")
    if entry and not (root / entry).is_file():
        missing.append(entry)
    for name in (release_contract or {}).get("required_config", []):
        if not (root / name).is_file():
            missing.append(name)
    return CheckResult("REQUIRED_RUNTIME_CONTRACT", "FAIL" if missing else "PASS",
                       {"missing": missing})


def evaluate(target_path, *, venture_id, deployment_target_id, expected_tree_hash,
             release_contract) -> list:
    """Run all required deployment checks; return ordered CheckResults."""
    rc = dict(release_contract or {})
    return [
        check_venture_isolation(target_path, venture_id, deployment_target_id),
        check_target_exists(target_path),
        check_release_identity(target_path, expected_tree_hash),
        check_health(target_path, rc.get("health_contract")),
        check_runtime_contract(target_path, rc),
    ]
