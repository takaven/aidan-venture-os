"""Interpretations: reasoning over Claims; never evidence, never mutating Claims."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import IdempotencyConflictError
from aidan_core.research import claims, interpretations

from conftest import research_claim


def _interp(conn, vid, *, key="i1", statement="reasoning"):
    return interpretations.create_interpretation(
        conn, vid, statement=statement, interpretation_key=key, produced_by="model.x"
    ).interpretation_id


def test_interpretation_links_to_claim(migrated):
    vid = ventures.create_venture(migrated, slug="in-1")
    cid, _ = research_claim(migrated, vid, key="c1", stance="SUPPORTS")
    iid = _interp(migrated, vid)
    assert interpretations.link_claim(migrated, interpretation_id=iid, claim_id=cid) is True


def test_interpretation_is_not_evidence(migrated):
    # An interpretation lives in its own table; no evidence_record is created for it.
    vid = ventures.create_venture(migrated, slug="in-notev")
    _interp(migrated, vid)
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM evidence_record WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 0  # no source/observation/claim made just by interpreting


def test_interpretation_does_not_mutate_disputed_claim(migrated):
    vid = ventures.create_venture(migrated, slug="in-disp")
    cid, obs_a = research_claim(migrated, vid, key="c1", stance="SUPPORTS")
    # add a contradicting observation to make it DISPUTED
    _, obs_b = research_claim(migrated, vid, key="c2")  # separate claim's obs reused? build fresh obs:
    from aidan_core.research import observations, sources
    from aidan_core.research.adapters import AcquiredSource
    from datetime import datetime, timezone
    src = sources.ingest(migrated, vid, AcquiredSource(locator="L", source_type="WEB_PAGE", content="x",
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), retrieved_by="a", acquisition_key="src-extra")).evidence_record_id
    obs_c = observations.create_observation(migrated, vid, source_evidence_id=src, statement="contra", observation_key="obs-extra").evidence_record_id
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs_c, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "DISPUTED"

    iid = _interp(migrated, vid, statement="I resolve this in favour of support")
    interpretations.link_claim(migrated, interpretation_id=iid, claim_id=cid)
    # Reasoning does NOT change the structural state or the underlying relations.
    assert claims.claim_state(migrated, cid) == "DISPUTED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM claim_evidence WHERE claim_id = %s", (cid,))
        assert cur.fetchone()[0] == 2


def test_cross_venture_link_rejected(migrated):
    a = ventures.create_venture(migrated, slug="in-va")
    b = ventures.create_venture(migrated, slug="in-vb")
    cid_b, _ = research_claim(migrated, b, key="cb")
    iid_a = _interp(migrated, a)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        interpretations.link_claim(migrated, interpretation_id=iid_a, claim_id=cid_b)


def test_append_only_and_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="in-idem")
    a = interpretations.create_interpretation(migrated, vid, statement="S", interpretation_key="k", produced_by="m")
    b = interpretations.create_interpretation(migrated, vid, statement="S", interpretation_key="k", produced_by="m")
    assert b.created is False and b.interpretation_id == a.interpretation_id
    with pytest.raises(IdempotencyConflictError):
        interpretations.create_interpretation(migrated, vid, statement="DIFFERENT", interpretation_key="k", produced_by="m")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE interpretation SET statement = 'x' WHERE id = %s", (a.interpretation_id,))
