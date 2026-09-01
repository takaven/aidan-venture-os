"""Deterministic deployment verification checks (Gate 6 Slice 2; provider-neutral read-back).

These run inside the trusted verifier boundary over a ``DeploymentObservation`` — the observed
deployment state read back INDEPENDENTLY of the worker. A worker's self-report is never consulted.
The observation comes from a :class:`~aidan_core.deploy.observe.DeploymentObserver`: the default
``LocalTargetObserver`` reads a controlled LOCAL directory (Slices 1-3; proves the architecture, NOT
cloud), while a future real provider observer produces the SAME observation shape from an external
read-back. The five checks below are pure functions over the observation, so local and real
verification run identical logic.

Target layout the local observer reads (per venture-isolated deployment target):
    <target_path>/release/...        the deployed candidate tree (must match release identity)
    <target_path>/.deploy/health     a bounded deterministic health marker

``observed_tree_hash`` reproduces the exact formula ``build.manifest`` used for a candidate tree,
so the observed deployed tree hash can be compared to the frozen ``release_candidate`` identity.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .observe import (
    DeploymentObservation,
    LocalTargetObserver,
    ObservedFile,
    observed_tree_hash,
)

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
    """Kernel-derived hash of a deployed tree on the local filesystem (backward-compatible helper;
    identical formula to ``observe.observed_tree_hash``)."""
    if not release_root.is_dir():
        return None
    files = []
    for p in sorted(release_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(release_root).as_posix()
            files.append(ObservedFile(rel, _sha256(p.read_bytes())))
    return observed_tree_hash(tuple(files))


# ---- pure deterministic checks over an OBSERVATION (provider-neutral) ----------------
def _obs_isolation(observation: DeploymentObservation, venture_id, deployment_target_id) -> CheckResult:
    # The observed target handle must resolve under this venture's + target's namespace. The handle
    # is a "/"-delimited namespace (a local path, or a real observer's normalized target ref).
    parts = str(observation.isolation_identity).split("/")
    ok = str(venture_id) in parts and str(deployment_target_id) in parts
    return CheckResult("VENTURE_TARGET_ISOLATION", "PASS" if ok else "FAIL",
                       {"venture_id": str(venture_id)})


def _obs_target_exists(observation: DeploymentObservation) -> CheckResult:
    ok = bool(observation.files)
    return CheckResult("TARGET_EXISTS", "PASS" if ok else "FAIL",
                       {"observed_files": len(observation.files)})


def _obs_release_identity(observation: DeploymentObservation, expected_tree_hash) -> CheckResult:
    observed = observed_tree_hash(observation.files)
    ok = observed is not None and observed == expected_tree_hash
    return CheckResult("RELEASE_IDENTITY", "PASS" if ok else "FAIL",
                       {"expected": expected_tree_hash, "observed": observed})


def _obs_health(observation: DeploymentObservation, health_contract) -> CheckResult:
    marker = observation.health_marker
    if marker is None:
        return CheckResult("HEALTH", "FAIL", {"reason": "no health marker"})
    expected = (health_contract or {}).get("marker_content")
    if expected is not None and str(marker).strip() != str(expected):
        return CheckResult("HEALTH", "FAIL", {"reason": "marker content mismatch"})
    return CheckResult("HEALTH", "PASS", {})


def _obs_runtime_contract(observation: DeploymentObservation, release_contract) -> CheckResult:
    present = {f.path for f in observation.files}
    missing = []
    entry = (release_contract or {}).get("entry_artifact")
    if entry and entry not in present:
        missing.append(entry)
    for name in (release_contract or {}).get("required_config", []):
        if name not in present:
            missing.append(name)
    return CheckResult("REQUIRED_RUNTIME_CONTRACT", "FAIL" if missing else "PASS",
                       {"missing": missing})


def evaluate_observation(observation: DeploymentObservation, *, venture_id, deployment_target_id,
                         expected_tree_hash, release_contract) -> list:
    """Run all required deployment checks over an observed deployment state. Provider-neutral: the
    observation may come from the local target OR a real external read-back — the logic is identical."""
    rc = dict(release_contract or {})
    return [
        _obs_isolation(observation, venture_id, deployment_target_id),
        _obs_target_exists(observation),
        _obs_release_identity(observation, expected_tree_hash),
        _obs_health(observation, rc.get("health_contract")),
        _obs_runtime_contract(observation, rc),
    ]


# ---- backward-compatible LOCAL-path entry points (Slices 1-3) ------------------------
def check_venture_isolation(target_path, venture_id, deployment_target_id) -> CheckResult:
    return _obs_isolation(LocalTargetObserver(target_path).observe(), venture_id, deployment_target_id)


def check_target_exists(target_path) -> CheckResult:
    return _obs_target_exists(LocalTargetObserver(target_path).observe())


def check_release_identity(target_path, expected_tree_hash) -> CheckResult:
    return _obs_release_identity(LocalTargetObserver(target_path).observe(), expected_tree_hash)


def check_health(target_path, health_contract) -> CheckResult:
    return _obs_health(LocalTargetObserver(target_path).observe(), health_contract)


def check_runtime_contract(target_path, release_contract) -> CheckResult:
    return _obs_runtime_contract(LocalTargetObserver(target_path).observe(), release_contract)


def evaluate(target_path, *, venture_id, deployment_target_id, expected_tree_hash,
             release_contract) -> list:
    """Run all required deployment checks over the controlled LOCAL target (default observer)."""
    observation = LocalTargetObserver(target_path).observe()
    return evaluate_observation(
        observation, venture_id=venture_id, deployment_target_id=deployment_target_id,
        expected_tree_hash=expected_tree_hash, release_contract=release_contract)
