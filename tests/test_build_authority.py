"""Gate 5 / Slice 1 — governed venture build authority + venture repository.

Proves the smallest Gate 5 foundation: a BUILD decision is not direct coding
authority; an immutable, venture-specific build_spec (bound to the genuine Gate 3
BUILD authority) and an isolated venture repository must exist before a builder —
an ordinary Gate 4 WorkerAdapter — may be dispatched through the existing Factory
runtime; authorization used for dispatch must be fresh and post-spec; and a
worker's self-report carries no quality/lifecycle/deploy authority.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import approvals, execution
from aidan_core.build import repository as repo_mod
from aidan_core.build import runtime as build_runtime
from aidan_core.build import spec as build_spec_mod
from aidan_core.build.runtime import BuildInput
from aidan_core.errors import (
    ApprovalRequiredError,
    BuildAuthorityError,
    IdempotencyConflictError,
    InconsistentCanonicalStateError,
)
from aidan_core.factory import runtime as factory_runtime
from aidan_core.factory import spec as spec_mod

from build_fakes import (
    DEFAULT_INTENT,
    BuilderWorker,
    BuilderWorkerB,
    build_authority,
    freeze_default_build_spec,
)
from factory_fakes import registry_with

_CAPS = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]


def _register_repo(conn, vid, ref="venture://repo/default"):
    return repo_mod.register_venture_repository(conn, vid, repository_ref=ref)


def _dispatch(conn, aid, worker, *, timeout=60, max_attempts=1):
    return build_runtime.execute_build(
        conn, aid, registry=registry_with(worker), worker_kind=worker.kind,
        verifier_kind="structured-contract", capability_scope=_CAPS,
        timeout_seconds=timeout, max_attempts=max_attempts,
    )


def _proof_count(conn, aid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM proof_receipt WHERE action_request_id = %s", (aid,))
        return cur.fetchone()[0]


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


# ==========================================================================
# build_spec authority + provenance (matrix 1-4)
# ==========================================================================
def test_1_build_action_may_receive_build_spec(migrated):
    auth = build_authority(migrated, slug="g5-1")
    res = freeze_default_build_spec(migrated, auth)
    assert res.created is True and res.build_spec_id
    row = build_spec_mod.get_build_spec(migrated, auth.action_id)
    assert row is not None and row[6] == DEFAULT_INTENT["buyer"]


def test_2_non_build_action_rejected(migrated):
    # A genuine VALIDATE decision chain: freezing a build_spec must fail because the
    # decision is not BUILD — not because of unrelated FK setup.
    auth = build_authority(migrated, slug="g5-2", decision="VALIDATE", key="v")
    with pytest.raises(InconsistentCanonicalStateError):
        build_spec_mod.create_build_spec(
            migrated, auth.action_id, source_investment_decision_id=auth.decision_id,
            source_recommendation_id=auth.recommendation_id, opportunity_id=auth.opportunity_id,
            **DEFAULT_INTENT,
        )
    assert build_spec_mod.get_build_spec(migrated, auth.action_id) is None


def test_3_concrete_gate3_provenance_recorded(migrated):
    auth = build_authority(migrated, slug="g5-3")
    freeze_default_build_spec(migrated, auth)
    row = build_spec_mod.get_build_spec(migrated, auth.action_id)
    # source_investment_decision_id / source_recommendation_id / opportunity_id
    assert str(row[3]) == str(auth.decision_id)
    assert str(row[4]) == str(auth.recommendation_id)
    assert str(row[5]) == str(auth.opportunity_id)


def test_4_cross_venture_provenance_rejected(migrated):
    a = build_authority(migrated, slug="g5-4a", key="A")
    b = build_authority(migrated, slug="g5-4b", key="B")
    # Reference venture B's BUILD decision while freezing for venture A's action.
    with pytest.raises(InconsistentCanonicalStateError):
        build_spec_mod.create_build_spec(
            migrated, a.action_id, source_investment_decision_id=b.decision_id,
            source_recommendation_id=a.recommendation_id, opportunity_id=a.opportunity_id,
            **DEFAULT_INTENT,
        )


# ==========================================================================
# build_spec immutability + idempotency (matrix 5-9)
# ==========================================================================
def test_5_exact_replay_converges(migrated):
    auth = build_authority(migrated, slug="g5-5")
    first = freeze_default_build_spec(migrated, auth)
    again = freeze_default_build_spec(migrated, auth)
    assert again.created is False and again.build_spec_id == first.build_spec_id
    assert again.spec_hash == first.spec_hash


def test_6_changed_intent_conflicts(migrated):
    auth = build_authority(migrated, slug="g5-6")
    first = freeze_default_build_spec(migrated, auth)
    with pytest.raises(IdempotencyConflictError):
        freeze_default_build_spec(migrated, auth, primary_workflow="a completely different journey")
    # the immutable row is unchanged
    row = build_spec_mod.get_build_spec(migrated, auth.action_id)
    assert row[16] == first.spec_hash


def test_7_update_rejected(migrated):
    auth = build_authority(migrated, slug="g5-7")
    res = freeze_default_build_spec(migrated, auth)
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE build_spec SET buyer = 'x' WHERE id = %s", (res.build_spec_id,))


def test_8_delete_rejected(migrated):
    auth = build_authority(migrated, slug="g5-8")
    res = freeze_default_build_spec(migrated, auth)
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM build_spec WHERE id = %s", (res.build_spec_id,))


def test_9_spec_hash_deterministic():
    common = dict(
        venture_id="v", action_request_id="a", source_investment_decision_id="d",
        source_recommendation_id="r", opportunity_id="o", **DEFAULT_INTENT,
    )
    h1 = build_spec_mod.compute_build_spec_hash(**common)
    h2 = build_spec_mod.compute_build_spec_hash(**common)
    assert h1 == h2
    changed = dict(common, buyer="a different buyer")
    assert build_spec_mod.compute_build_spec_hash(**changed) != h1
    # capability set membership is semantic; order is not
    reordered = dict(common, required_capabilities=list(reversed(DEFAULT_INTENT["required_capabilities"])))
    assert build_spec_mod.compute_build_spec_hash(**reordered) == h1


# ==========================================================================
# venture_repository identity + isolation (matrix 10-14)
# ==========================================================================
def test_10_one_repository_registered(migrated):
    auth = build_authority(migrated, slug="g5-10")
    res = _register_repo(migrated, auth.venture_id, "venture://g5-10/app")
    assert res.created is True and res.repository_ref == "venture://g5-10/app"


def test_11_repo_exact_replay_converges(migrated):
    auth = build_authority(migrated, slug="g5-11")
    first = _register_repo(migrated, auth.venture_id, "venture://g5-11/app")
    again = _register_repo(migrated, auth.venture_id, "venture://g5-11/app")
    assert again.created is False and again.venture_repository_id == first.venture_repository_id


def test_12_conflicting_registration_conflicts(migrated):
    auth = build_authority(migrated, slug="g5-12")
    _register_repo(migrated, auth.venture_id, "venture://g5-12/app")
    with pytest.raises(IdempotencyConflictError):
        _register_repo(migrated, auth.venture_id, "venture://g5-12/OTHER")


def test_13_repository_backs_at_most_one_venture(migrated):
    a = build_authority(migrated, slug="g5-13a", key="A")
    b = build_authority(migrated, slug="g5-13b", key="B")
    _register_repo(migrated, a.venture_id, "venture://shared/app")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _register_repo(migrated, b.venture_id, "venture://shared/app")


def test_14_canonical_os_repo_rejected(migrated):
    auth = build_authority(migrated, slug="g5-14")
    for os_ref in (
        "takaven/aidan-venture-os",
        "aidan-venture-os",
        "C:/Users/isuda/Dev/aidan-venture-os",
        "/home/x/aidan-venture-os-gate0-source",
    ):
        with pytest.raises(BuildAuthorityError):
            repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref=os_ref)
    with pytest.raises(ValueError):
        repo_mod.register_venture_repository(migrated, auth.venture_id, repository_ref="  ")


# ==========================================================================
# Gate 4 execution binding + composition (matrix 15-17)
# ==========================================================================
def test_15_composition_requires_build_spec_and_repo(migrated):
    auth = build_authority(migrated, slug="g5-15")
    # no build_spec yet
    with pytest.raises(BuildAuthorityError):
        build_runtime.prepare_build_execution(
            migrated, auth.action_id, worker_kind="builder-a", verifier_kind="structured-contract",
            capability_scope=_CAPS, timeout_seconds=60, max_attempts=1,
        )
    # build_spec exists but no repository
    freeze_default_build_spec(migrated, auth)
    with pytest.raises(BuildAuthorityError):
        build_runtime.prepare_build_execution(
            migrated, auth.action_id, worker_kind="builder-a", verifier_kind="structured-contract",
            capability_scope=_CAPS, timeout_seconds=60, max_attempts=1,
        )


def test_16_17_execution_spec_binds_exact_build_identity(migrated):
    auth = build_authority(migrated, slug="g5-16")
    bs = freeze_default_build_spec(migrated, auth)
    repo = _register_repo(migrated, auth.venture_id, "venture://g5-16/app")
    dispatch = build_runtime.prepare_build_execution(
        migrated, auth.action_id, worker_kind="builder-a", verifier_kind="structured-contract",
        capability_scope=_CAPS, timeout_seconds=60, max_attempts=1,
    )
    task_payload = spec_mod.get_execution_spec(migrated, auth.action_id)[4]
    block = task_payload["build"]
    assert block["build_spec_id"] == str(bs.build_spec_id)
    assert block["build_spec_hash"] == bs.spec_hash          # matching hash bound
    assert block["venture_repository_id"] == str(repo.venture_repository_id)
    assert block["repository_ref"] == "venture://g5-16/app"
    assert block["intent"]["primary_workflow"] == DEFAULT_INTENT["primary_workflow"]
    # idempotent: a second prepare converges on the same immutable execution_spec
    again = build_runtime.prepare_build_execution(
        migrated, auth.action_id, worker_kind="builder-a", verifier_kind="structured-contract",
        capability_scope=_CAPS, timeout_seconds=60, max_attempts=1,
    )
    assert again.execution_spec_created is False
    assert again.execution_spec_id == dispatch.execution_spec_id


# ==========================================================================
# Authorization is fresh + post-spec (matrix 18-20)
# ==========================================================================
def test_18_19_20_pre_spec_authorization_insufficient(migrated):
    auth = build_authority(
        migrated, slug="g5-18", autonomy_level=0, required_autonomy=2, amount=10, grant=1000, key="ap"
    )
    freeze_default_build_spec(migrated, auth)
    _register_repo(migrated, auth.venture_id, "venture://g5-18/app")

    # An approval opened BEFORE the execution spec exists (pre-spec) is approved...
    pre = execution.request_execution(migrated, auth.action_id)
    approvals.approve(migrated, pre.approval_id, decided_by="board")

    worker = BuilderWorker()
    # ...but it cannot authorize dispatch of the later-frozen spec.
    with pytest.raises(ApprovalRequiredError):
        _dispatch(migrated, auth.action_id, worker)
    assert worker.calls == 0

    # A fresh, post-spec approval authorizes dispatch.
    fresh = factory_runtime.request_dispatch_authorization(migrated, auth.action_id)
    approvals.approve(migrated, fresh.approval_id, decided_by="board")
    dispatch, result = _dispatch(migrated, auth.action_id, worker)
    assert result.dispatched is True and worker.calls == 1


# ==========================================================================
# Builder is a Gate 4 WorkerAdapter; no DB/quality/lifecycle authority (21-25, 27, 30, 31)
# ==========================================================================
def test_21_22_builder_dispatches_via_workeradapter_no_db(migrated):
    auth = build_authority(migrated, slug="g5-21")
    freeze_default_build_spec(migrated, auth)
    _register_repo(migrated, auth.venture_id, "venture://g5-21/app")
    worker = BuilderWorker()
    _dispatch(migrated, auth.action_id, worker)
    assert worker.calls == 1
    req = worker.last_request
    assert not hasattr(req, "conn") and not hasattr(req, "connection")
    # venture-specific intent actually reached the worker through the canonical request
    bi = BuildInput.from_worker_request(req)
    assert bi.intent["buyer"] == DEFAULT_INTENT["buyer"]
    assert bi.repository_ref == "venture://g5-21/app"
    assert req.workspace_ref == "venture://g5-21/app"


def test_23_worker_self_report_is_inert(migrated):
    auth = build_authority(migrated, slug="g5-23")
    bs = freeze_default_build_spec(migrated, auth)
    _register_repo(migrated, auth.venture_id, "venture://g5-23/app")
    worker = BuilderWorker(structured_output={
        "quality_pass": True, "lifecycle": "OPERATING", "merge": True, "deploy": True,
        "change_build_spec": {"buyer": "someone else"}, "broaden_capabilities": ["ADMIN"],
    })
    _dispatch(migrated, auth.action_id, worker)
    assert execution.get_status(migrated, auth.action_id) != "SUCCEEDED"
    assert _proof_count(migrated, auth.action_id) == 0            # no VERIFIED proof
    assert _lifecycle(migrated, auth.venture_id) == "DISCOVERED"  # lifecycle unchanged
    assert build_spec_mod.get_build_spec(migrated, auth.action_id)[16] == bs.spec_hash  # spec unchanged


def test_24_31_provider_neutral_same_runtime(migrated):
    a = build_authority(migrated, slug="g5-24a", key="A")
    freeze_default_build_spec(migrated, a)
    _register_repo(migrated, a.venture_id, "venture://g5-24a/app")
    b = build_authority(migrated, slug="g5-24b", key="B")
    freeze_default_build_spec(migrated, b)
    _register_repo(migrated, b.venture_id, "venture://g5-24b/app")

    wa, wb = BuilderWorker(), BuilderWorkerB()
    _, ra = _dispatch(migrated, a.action_id, wa)
    _, rb = _dispatch(migrated, b.action_id, wb)
    assert ra.dispatched and rb.dispatched
    assert wa.calls == 1 and wb.calls == 1
    assert ra.worker_kind == "builder-a" and rb.worker_kind == "builder-b"


def test_25_no_quality_manifest_deploy_lifecycle_side_effects(migrated):
    auth = build_authority(migrated, slug="g5-25")
    freeze_default_build_spec(migrated, auth)
    _register_repo(migrated, auth.venture_id, "venture://g5-25/app")
    _dispatch(migrated, auth.action_id, BuilderWorker())
    assert _proof_count(migrated, auth.action_id) == 0
    assert _lifecycle(migrated, auth.venture_id) == "DISCOVERED"
    # no Slice-2+ tables exist yet
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.build_manifest'), to_regclass('public.quality_assessment')")
        assert cur.fetchone() == (None, None)


# ==========================================================================
# Substrate deferred (Option B) — no fake provenance (matrix 26, 32)
# ==========================================================================
def test_26_32_substrate_deferred_no_fake_provenance(migrated):
    # No substrate_release table/entity exists, and build_spec carries no substrate
    # version field: substrate identity is deferred to Slice 2 (Option B).
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.substrate_release')")
        assert cur.fetchone()[0] is None
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'build_spec' AND column_name LIKE '%substrate%'"
        )
        assert cur.fetchone()[0] == 0
    assert not hasattr(build_spec_mod, "substrate_version")
    # the hash function takes no substrate parameter
    import inspect

    params = inspect.signature(build_spec_mod.compute_build_spec_hash).parameters
    assert not any("substrate" in p for p in params)
