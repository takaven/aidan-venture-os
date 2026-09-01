"""Provider-neutral deployment OBSERVATION seam (Gate 6 real-deploy readiness).

The deterministic deployment verifier must establish observed deployment state INDEPENDENTLY of
the worker's self-report. Gate-6 Slices 1-3 proved that architecture against a controlled LOCAL
target directory. A genuine ``REAL_EXTERNAL`` deployment reads that state back from an external
provider instead — but the five required checks (isolation, target-exists, release-identity,
health, runtime-contract) and the kernel-owned identity hash must stay byte-identical and
provider-neutral.

This module is that seam. A ``DeploymentObserver`` returns a ``DeploymentObservation`` — the
observed deployment state as plain data — and the checks are pure functions over it. The default
``LocalTargetObserver`` reproduces the exact local-directory read-back of Slices 1-3 (so every
existing proof holds unchanged); a future real provider observer produces the SAME observation
shape from an external read-back (e.g. fetching the deployed artifact manifest + a health/runtime
probe). No provider is selected here and nothing performs network I/O; the observer is the single
injection point where a real read-back would later plug in.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from ..actions import canonical_payload_hash

# Provider-contact/effect evidence for a read-back, mirroring the honest-observability doctrine of
# the Codex provider path: crossing a boundary is not proof the external target was actually reached.
CONTACT_OBSERVED = "OBSERVED"
CONTACT_NOT_OBSERVED = "NOT_OBSERVED"
CONTACT_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ObservedFile:
    """One file observed in the deployed release tree, with its kernel-computed content hash."""
    path: str
    sha256: str


@dataclass(frozen=True)
class DeploymentObservation:
    """Observed deployment state, read back INDEPENDENTLY of the worker (never its self-report).

    Provider-neutral: a local observer fills this from a controlled directory; a real provider
    observer fills the identical shape from an external read-back. The verifier's checks are pure
    functions over this value, so local and real verification run the SAME logic.
    """
    isolation_identity: str                 # the target handle actually read back from (path / ext ref)
    files: tuple[ObservedFile, ...]         # observed deployed release tree (empty tuple if none)
    health_marker: Optional[str]            # observed health signal (marker/probe), or None if absent
    target_present: bool = True             # whether the deployment target could be read back at all
    contact: str = CONTACT_UNKNOWN          # OBSERVED | NOT_OBSERVED | UNKNOWN


@runtime_checkable
class DeploymentObserver(Protocol):
    """Reads back observed deployment state from a target. Implementations MUST NOT consult the
    worker's self-report; they observe the actual deployed target only."""

    def observe(self) -> DeploymentObservation: ...


def observed_tree_hash(files: tuple[ObservedFile, ...]) -> Optional[str]:
    """Kernel-derived hash of an observed release tree — the SAME formula ``build.manifest`` and the
    Slice-2 local read-back use, so observed identity is comparable to the frozen release identity.
    Returns None for an empty/absent tree (no deployed files observed)."""
    if not files:
        return None
    entries = sorted(({"path": f.path, "sha256": f.sha256} for f in files), key=lambda e: e["path"])
    return canonical_payload_hash({"files": entries})


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class LocalTargetObserver:
    """Default observer: reads the controlled LOCAL target directory exactly as Slices 1-3 did.

        <target_path>/release/...        the deployed candidate tree
        <target_path>/.deploy/health     a bounded deterministic health marker

    Proves the architecture (release identity / health / runtime contract / isolation), NOT cloud.
    """

    def __init__(self, target_path: str):
        self.target_path = target_path

    def observe(self) -> DeploymentObservation:
        root = Path(self.target_path) / "release"
        files: list[ObservedFile] = []
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(root).as_posix()
                    files.append(ObservedFile(rel, _sha256(p.read_bytes())))
        hf = Path(self.target_path) / ".deploy" / "health"
        health = hf.read_text(encoding="utf-8").strip() if hf.is_file() else None
        # A local directory read either resolves or does not; there is no external provider to
        # "contact", so a successful local read-back is reported as OBSERVED, an absent one UNKNOWN.
        present = Path(self.target_path).exists()
        return DeploymentObservation(
            isolation_identity=Path(self.target_path).as_posix(),
            files=tuple(files),
            health_marker=health,
            target_present=present,
            contact=CONTACT_OBSERVED if present else CONTACT_UNKNOWN,
        )
