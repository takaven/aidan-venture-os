"""Gate 6 — governed release authority + controlled deployment.

Slice 1 establishes the authority boundary between a Gate-5 quality-passed candidate
and any deployment execution: an immutable, quality-qualified ``release_candidate``
(1:1 with the deploy ActionRequest), a venture-owned ``deployment_target``, and a
composition that binds both into the existing Gate 4 execution runtime. The deploy
worker is an ordinary Gate 4 ``WorkerAdapter`` — no second runtime.

No external deployment, no deployment verification, no Proof Receipt, no lifecycle
transition, and no deployment_record live in Slice 1.
"""
from __future__ import annotations

from .release import ReleaseResult, create_release_candidate, get_release_candidate
from .runtime import (
    DeployDispatch,
    DeployInput,
    deploy_target_path,
    execute_deploy,
    prepare_deploy_execution,
    verify_deploy,
)
from .state import (
    latest_verified_deployment,
    promote_verified_deployment,
    verified_deployment,
)
from .target import TargetResult, get_deployment_target, register_deployment_target
from .verifiers import DeploymentReleaseVerifier, deploy_verifier_registry

__all__ = [
    "TargetResult",
    "register_deployment_target",
    "get_deployment_target",
    "ReleaseResult",
    "create_release_candidate",
    "get_release_candidate",
    "DeployInput",
    "DeployDispatch",
    "prepare_deploy_execution",
    "execute_deploy",
    "verify_deploy",
    "deploy_target_path",
    "DeploymentReleaseVerifier",
    "deploy_verifier_registry",
    "verified_deployment",
    "latest_verified_deployment",
    "promote_verified_deployment",
]
