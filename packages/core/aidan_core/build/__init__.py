"""Gate 5 — governed venture build authority.

Slice 1 establishes the immutable authority boundary between a governed Gate 3
BUILD decision and any builder execution: an immutable, venture-specific
``build_spec`` (bound 1:1 to the BUILD ActionRequest), an isolated
``venture_repository`` identity, and a composition layer that binds both into the
existing Gate 4 execution runtime. The Builder is a Gate 4 ``WorkerAdapter`` — no
second worker/execution runtime is introduced here.

No product-quality engines, no build manifest, no substrate implementation, no
deployment, and no lifecycle transition live in Slice 1.
"""
from __future__ import annotations

from .manifest import ManifestResult, capture_build_manifest, get_build_manifest
from .repository import RepositoryResult, register_venture_repository
from .runtime import (
    BuildInput,
    capture_and_check_build,
    execute_build,
    prepare_build_execution,
)
from .spec import BuildSpecResult, create_build_spec, get_build_spec
from .substrate import SubstrateReleaseResult, create_substrate_release, get_substrate_release
from .technical import TechnicalCheckResult, run_technical_checks, technical_verdict

__all__ = [
    "BuildSpecResult",
    "create_build_spec",
    "get_build_spec",
    "RepositoryResult",
    "register_venture_repository",
    "BuildInput",
    "prepare_build_execution",
    "execute_build",
    "SubstrateReleaseResult",
    "create_substrate_release",
    "get_substrate_release",
    "ManifestResult",
    "capture_build_manifest",
    "get_build_manifest",
    "TechnicalCheckResult",
    "run_technical_checks",
    "technical_verdict",
    "capture_and_check_build",
]
