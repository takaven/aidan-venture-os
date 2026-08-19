"""Gate 6 / Slice 3 — proof-gated deployment lifecycle projection + failure/recovery.

A deterministically VERIFIED deployment proof (verification_type=DEPLOYMENT_RELEASE)
is the sole authority that projects BUILDING -> OPERATING, via the existing lifecycle
kernel. Worker/verifier claims are inert; a generic worker-execution proof is
insufficient; wrong venture/attempt/release proofs reject; deployment failure never
changes Gate-5 quality, never auto-kills, and never promotes. No deployment_record,
no new migration, no market truth.
"""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import killswitch, lifecycle
from aidan_core.build import quality as quality_mod
from aidan_core.deploy import release as release_mod
from aidan_core.deploy import runtime as deploy_runtime
from aidan_core.deploy import state as deploy_state
from aidan_core.errors import DeployAuthorityError, ExecutionBlockedError, IllegalTransitionError
from factory_fakes import registry_with

from deploy_fakes import DeployBundleWorker, setup_deploy, to_building


def _release(conn, s, **kw):
    return release_mod.create_release_candidate(
        conn, s.deploy_action_id, build_manifest_id=s.build_manifest_id,
        deployment_target_id=s.target_id, **kw)


def _deploy(conn, s, *, mode="compliant", max_attempts=1):
    w = DeployBundleWorker(mode=mode)
    deploy_runtime.execute_deploy(conn, s.deploy_action_id, registry=registry_with(w),
                                  worker_kind=w.kind, max_attempts=max_attempts)
    return w


def _verify(conn, s):
    return deploy_runtime.verify_deploy(conn, s.deploy_action_id, actual_cost=0)


def _lifecycle(conn, vid):
    with conn.cursor() as cur:
        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (vid,))
        return cur.fetchone()[0]


def _verified(conn, slug, *, key=None):
    """A venture in BUILDING with a compliant, VERIFIED deployment."""
    s = setup_deploy(conn, slug, key=key or slug)
    _release(conn, s)
    to_building(conn, s.venture_id)
    _deploy(conn, s)
    out = _verify(conn, s)
    assert out.verified is True
    return s


# ==========================================================================
# Lifecycle is gated on a deployment-specific VERIFIED proof (matrix 1-5, 10)
# ==========================================================================
def test_1_quality_pass_no_release_proof_stays_building(migrated):
    s = setup_deploy(migrated, "l1")
    to_building(migrated, s.venture_id)
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"


def test_2_3_worker_deployed_claim_and_execution_success_insufficient(migrated):
    s = setup_deploy(migrated, "l2")
    _release(migrated, s)
    to_building(migrated, s.venture_id)
    _deploy(migrated, s, mode="nothing")  # worker claims deployed but wrote nothing; execution ok
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"


def test_4_verifier_rejected_stays_building(migrated):
    s = setup_deploy(migrated, "l4")
    _release(migrated, s)
    to_building(migrated, s.venture_id)
    _deploy(migrated, s, mode="wrong_bytes")
    assert _verify(migrated, s).verified is False
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"


def test_5_generic_non_deployment_proof_rejected(migrated):
    # a completed BUILD action carries a STRUCTURED_CONTRACT proof, not a deployment proof;
    # it can never authorize an OPERATING projection (no release_candidate + wrong proof kind)
    from aidan_core.factory import runtime as factory_runtime
    from build_fakes import full_eval
    r = full_eval(migrated, "l5", product_manifest=None)
    factory_runtime.verify_and_complete(migrated, r.auth.action_id, actual_cost=0)  # STRUCTURED_CONTRACT proof
    to_building(migrated, r.auth.venture_id)
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, r.auth.action_id)
    assert _lifecycle(migrated, r.auth.venture_id) == "BUILDING"


def test_10_verified_deployment_promotes_to_operating(migrated):
    s = _verified(migrated, "l10")
    out = deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert out["outcome"] == "promoted" and out["state"] == "OPERATING"
    assert _lifecycle(migrated, s.venture_id) == "OPERATING"


# ==========================================================================
# Idempotent projection; no duplicate history (matrix 11-12)
# ==========================================================================
def test_11_12_promotion_idempotent_no_duplicate_history(migrated):
    s = _verified(migrated, "l11")
    deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    again = deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)  # converges
    assert again["outcome"] == "already_operating"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_event WHERE venture_id = %s "
                    "AND event_type = 'venture.lifecycle_transition' AND payload->>'to' = 'OPERATING'",
                    (s.venture_id,))
        assert cur.fetchone()[0] == 1  # exactly one OPERATING transition recorded


# ==========================================================================
# Provenance: wrong proof rejected (matrix 6-9)
# ==========================================================================
def test_6_9_cross_action_and_venture_isolation(migrated):
    a = _verified(migrated, "l6a", key="l6a")
    b = setup_deploy(migrated, "l6b", key="l6b")  # different venture, no verified deployment
    to_building(migrated, b.venture_id)
    # promoting B (which has no deployment proof) must fail and not touch A
    with pytest.raises(DeployAuthorityError):
        deploy_state.promote_verified_deployment(migrated, b.deploy_action_id)
    # A's proof cannot promote B; each promotion is scoped to its own action's venture
    deploy_state.promote_verified_deployment(migrated, a.deploy_action_id)
    assert _lifecycle(migrated, a.venture_id) == "OPERATING"
    assert _lifecycle(migrated, b.venture_id) == "BUILDING"


def test_promotion_requires_building_state(migrated):
    # a verified deployment on a venture NOT in BUILDING cannot skip the state machine
    s = setup_deploy(migrated, "lstate")
    _release(migrated, s)
    _deploy(migrated, s)
    assert _verify(migrated, s).verified is True
    assert _lifecycle(migrated, s.venture_id) == "DISCOVERED"
    with pytest.raises(IllegalTransitionError):  # DISCOVERED -> OPERATING not permitted
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)


# ==========================================================================
# Governance recheck at promotion (kill after proof)
# ==========================================================================
def test_kill_after_proof_blocks_promotion(migrated):
    s = _verified(migrated, "lkill")
    killswitch.engage_global(migrated, engaged_by="op")
    with pytest.raises(ExecutionBlockedError):
        deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"


# ==========================================================================
# Deployment failure semantics (matrix 13-15)
# ==========================================================================
def test_13_14_15_failure_preserves_quality_release_and_no_kill(migrated):
    s = setup_deploy(migrated, "l13")
    rc = _release(migrated, s)
    to_building(migrated, s.venture_id)
    _deploy(migrated, s, mode="wrong_bytes")
    assert _verify(migrated, s).verified is False
    # Gate-5 quality unchanged
    for d in ("PRODUCT", "EXPERIENCE", "COMMERCIAL", "ANTIGENERIC"):
        assert quality_mod.dimension_verdict(migrated, s.build_manifest_id, d) == "PASS"
    assert quality_mod.overall_verdict(migrated, s.build_manifest_id) == "PASS"
    # release unchanged; lifecycle unchanged; not killed
    assert release_mod.get_release_candidate(migrated, s.deploy_action_id)[8] == rc.release_hash
    assert _lifecycle(migrated, s.venture_id) == "BUILDING"
    assert killswitch.is_killed(migrated, s.venture_id) is False


# ==========================================================================
# Retry -> promote after successful attempt (matrix 16-19, 27)
# ==========================================================================
def test_16_19_retry_then_promote(migrated):
    s = setup_deploy(migrated, "l16")
    rc = _release(migrated, s)
    to_building(migrated, s.venture_id)
    _deploy(migrated, s, mode="wrong_bytes", max_attempts=2)
    assert _verify(migrated, s).verified is False
    _deploy(migrated, s, mode="compliant", max_attempts=2)
    assert _verify(migrated, s).verified is True
    out = deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    assert out["state"] == "OPERATING"
    # same release across retry; two attempts retained
    assert release_mod.get_release_candidate(migrated, s.deploy_action_id)[8] == rc.release_hash
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_attempt WHERE action_request_id = %s", (s.deploy_action_id,))
        assert cur.fetchone()[0] == 2


# ==========================================================================
# Canonical deployment truth reconstructable from existing state (matrix 16, 29)
# ==========================================================================
def test_verified_deployment_reconstructable(migrated):
    s = _verified(migrated, "lrec")
    dep = deploy_state.verified_deployment(migrated, s.deploy_action_id)
    assert dep["venture_id"] == str(s.venture_id)
    assert dep["deployment_target_id"] == str(s.target_id)
    assert dep["build_manifest_id"] == str(s.build_manifest_id)
    latest = deploy_state.latest_verified_deployment(migrated, s.venture_id, s.target_id)
    assert latest["action_request_id"] == str(s.deploy_action_id)


def test_29_no_deployment_record_table(migrated):
    with migrated.cursor() as cur:
        cur.execute("SELECT to_regclass('public.deployment_record'), to_regclass('public.current_release')")
        assert cur.fetchone() == (None, None)


# ==========================================================================
# OPERATING is not market success (matrix 28)
# ==========================================================================
def test_28_operating_creates_no_market_truth(migrated):
    s = _verified(migrated, "l28")
    deploy_state.promote_verified_deployment(migrated, s.deploy_action_id)
    with migrated.cursor() as cur:
        for t in ("public.market_evidence", "public.revenue", "public.customer", "public.acquisition_result"):
            cur.execute("SELECT to_regclass(%s)", (t,))
            assert cur.fetchone()[0] is None
