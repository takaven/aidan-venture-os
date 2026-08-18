"""Kill Cases: adversarial reasoning, categorical, linked to Claims, append-only."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import IdempotencyConflictError
from aidan_core.research import claims, killcase, opportunities

from conftest import research_claim


def _opp(conn, vid, *, key="o1"):
    return opportunities.create_opportunity(conn, vid, opportunity_key=key).opportunity_id


def test_kill_case_cannot_reference_another_venture_opportunity(migrated):
    a = ventures.create_venture(migrated, slug="kc-va")
    b = ventures.create_venture(migrated, slug="kc-vb")
    opp_a = _opp(migrated, a)
    # Raw insert claiming venture b for a's opportunity -> composite FK violation.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO kill_case (opportunity_id, venture_id, kill_case_key, disposition, content_hash) "
                "VALUES (%s, %s, 'k', 'KILL', 'h')",
                (opp_a, b),
            )


def test_kill_case_idempotency_and_conflict(migrated):
    vid = ventures.create_venture(migrated, slug="kc-idem")
    opp = _opp(migrated, vid)
    a = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS")
    b = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS")
    assert b.created is False and b.kill_case_id == a.kill_case_id
    with pytest.raises(IdempotencyConflictError):
        killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="KILL")


def test_all_dimensions_and_categorical(migrated):
    vid = ventures.create_venture(migrated, slug="kc-dims")
    opp = _opp(migrated, vid)
    kc = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS").kill_case_id
    for dim in killcase.REQUIRED_DIMENSIONS:
        killcase.add_dimension(migrated, kill_case_id=kc, dimension=dim, assessment="LOW_RISK", rationale="r")
    assert killcase.missing_dimensions(migrated, kc) == set()
    # categorical only
    with pytest.raises(ValueError):
        killcase.add_dimension(migrated, kill_case_id=kc, dimension="REGULATION", assessment="0.9", rationale="r")
    # INSUFFICIENT_EVIDENCE is a valid dimension assessment (absence != LOW_RISK)
    vid2 = ventures.create_venture(migrated, slug="kc-insuf")
    opp2 = _opp(migrated, vid2)
    kc2 = killcase.create_kill_case(migrated, opportunity_id=opp2, kill_case_key="k", disposition="INSUFFICIENT_EVIDENCE").kill_case_id
    d = killcase.add_dimension(migrated, kill_case_id=kc2, dimension="MARKET_SIZE", assessment="INSUFFICIENT_EVIDENCE", rationale="unknown")
    assert d.created is True


def test_duplicate_dimension_deterministic(migrated):
    vid = ventures.create_venture(migrated, slug="kc-dup")
    opp = _opp(migrated, vid)
    kc = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS").kill_case_id
    a = killcase.add_dimension(migrated, kill_case_id=kc, dimension="REGULATION", assessment="MATERIAL_RISK", rationale="r")
    b = killcase.add_dimension(migrated, kill_case_id=kc, dimension="REGULATION", assessment="MATERIAL_RISK", rationale="r")
    assert b.created is False and b.dimension_id == a.dimension_id
    with pytest.raises(IdempotencyConflictError):
        killcase.add_dimension(migrated, kill_case_id=kc, dimension="REGULATION", assessment="SEVERE_RISK", rationale="r")


def test_dimension_links_to_claim_without_mutating_it(migrated):
    vid = ventures.create_venture(migrated, slug="kc-claim")
    cid, obs_a = research_claim(migrated, vid, key="c1", stance="SUPPORTS")
    # make it DISPUTED
    from aidan_core.research import observations, sources
    from aidan_core.research.adapters import AcquiredSource
    from datetime import datetime, timezone
    src = sources.ingest(migrated, vid, AcquiredSource(locator="L", source_type="WEB_PAGE", content="x",
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), retrieved_by="a", acquisition_key="src-x")).evidence_record_id
    obs_c = observations.create_observation(migrated, vid, source_evidence_id=src, statement="contra", observation_key="obs-x").evidence_record_id
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs_c, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "DISPUTED"

    opp = _opp(migrated, vid)
    kc = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS").kill_case_id
    dim = killcase.add_dimension(migrated, kill_case_id=kc, dimension="WTP_WEAKNESS", assessment="MATERIAL_RISK", rationale="r").dimension_id
    assert killcase.link_dimension_claim(migrated, dimension_id=dim, claim_id=cid) is True
    assert claims.claim_state(migrated, cid) == "DISPUTED"  # unchanged
    # rationale did not become an observation: only the 2 obs from claim setup exist.
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM observation WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 2


def test_kill_case_append_only(migrated):
    vid = ventures.create_venture(migrated, slug="kc-imm")
    opp = _opp(migrated, vid)
    kc = killcase.create_kill_case(migrated, opportunity_id=opp, kill_case_key="k", disposition="PROCEED_WITH_RISKS").kill_case_id
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE kill_case SET disposition = 'KILL' WHERE id = %s", (kc,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM kill_case WHERE id = %s", (kc,))
