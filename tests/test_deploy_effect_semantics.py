"""Consequential-effect semantics for the deploy path (Gate 6 real-deploy readiness).

A REAL_EXTERNAL deploy is a consequential external action. Gate-4 already fails such actions CLOSED:
an ambiguous consequential effect -> RECOVERY_REQUIRED (not auto-claimable, never blind-retried); a
known provider rejection -> FAILED (terminal). A deploy worker is an ordinary Gate-4 WorkerAdapter,
so it INHERITS these semantics unchanged. This suite proves that carry-through on the deploy path
using the real governed deploy chain (no external provider, no network) — the effect is simulated by
a worker that raises the canonical exception a real adapter would raise.

Proves: ambiguous effect -> RECOVERY_REQUIRED + no VERIFIED proof + promotion blocked + no blind
retry; known rejection -> FAILED + no OPERATING; and neither ever transitions BUILDING->OPERATING.
"""
from __future__ import annotations

import pytest

from aidan_core import execution
from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import state as deploy_state
from aidan_core.errors import (
    AmbiguousExternalEffectError,
    DeployAuthorityError,
    ExecutionBlockedError,
    ProviderExecutionFailure,
)
from factory_fakes import registry_with

from deploy_fakes import setup_deploy, to_building


class AmbiguousDeployWorker:
    """Simulates a real deploy whose external effect crossed AMBIGUOUSLY (the provider push may or
    may not have taken effect and cannot be reconciled). A real adapter raises this; it writes
    nothing to any target."""

    kind = "deploy-a"

    def __init__(self, **_):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise AmbiguousExternalEffectError("DEPLOY_EFFECT_AMBIGUOUS")


class KnownRejectDeployWorker:
    """Simulates a real deploy that reached the provider and got a KNOWN rejection (terminal, no
    blind retry). Modeled as a ProviderExecutionFailure, exactly like the Codex provider path."""

    kind = "deploy-a"

    def __init__(self, **_):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise ProviderExecutionFailure("DEPLOY_PROVIDER_REJECTED", failure_class="WORKER_ERROR",
                                       provider_contact="OBSERVED")


def _prepare(conn, slug, worker_cls):
    s = setup_deploy(conn, slug, key=slug)
    release_mod.create_release_candidate(
        conn, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
        deployment_target_id=s.target_id)
    to_building(conn, s.venture_id)
    w = worker_cls()
    deploy_runtime.execute_deploy(conn, s.deploy_action_id, registry=registry_with(w), worker_kind=w.kind)
    return s, w


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def test_ambiguous_external_effect_is_recovery_required_not_operating(migrated):
    s, w = _prepare(migrated, "dep-ambiguous", AmbiguousDeployWorker)
    assert w.calls == 1
    # The consequential boundary was crossed ambiguously -> fail CLOSED into RECOVERY_REQUIRED.
    assert execution.get_status(migrated, s.deploy_action_id) == "RECOVERY_REQUIRED"
    # No deployment-specific VERIFIED proof exists, so lifecycle cannot be promoted.
    assert deploy_state.verified_deployment(migrated, s.deploy_action_id) is None
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"


def test_recovery_required_deploy_is_never_blind_retried(migrated):
    s, w = _prepare(migrated, "dep-noretry", AmbiguousDeployWorker)
    assert execution.get_status(migrated, s.deploy_action_id) == "RECOVERY_REQUIRED"
    # A RECOVERY_REQUIRED action is not auto-claimable: a second dispatch must be refused, so the
    # consequential external effect can never be blind-re-issued.
    w2 = AmbiguousDeployWorker()
    with pytest.raises(ExecutionBlockedError):
        deploy_runtime.execute_deploy(migrated, s.deploy_action_id, registry=registry_with(w2),
                                      worker_kind=w2.kind)
    assert w2.calls == 0
    assert execution.get_status(migrated, s.deploy_action_id) == "RECOVERY_REQUIRED"


def test_known_provider_rejection_is_failed_not_operating(migrated):
    s, w = _prepare(migrated, "dep-rejected", KnownRejectDeployWorker)
    assert w.calls == 1
    # A KNOWN rejection is a true FAILED (terminal) — not RECOVERY_REQUIRED, not OPERATING.
    assert execution.get_status(migrated, s.deploy_action_id) == "FAILED"
    assert deploy_state.verified_deployment(migrated, s.deploy_action_id) is None
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"
