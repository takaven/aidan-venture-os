"""Assumptions: categorical uncertainty, links, no ActionRequest/capital effects."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import IdempotencyConflictError
from aidan_core.research import assumptions, interpretations

from conftest import research_claim


def _assume(conn, vid, *, key="a1", proposition="SMB WTP is strong", importance="HIGH", confidence="LOW"):
    return assumptions.create_assumption(
        conn, vid, proposition=proposition, assumption_key=key, importance=importance,
        confidence=confidence, consequence_if_false="thesis collapses",
        cheapest_test="interview 5 finance managers about WTP",
    ).assumption_id


def test_assumption_links_to_claim_and_interpretation(migrated):
    vid = ventures.create_venture(migrated, slug="as-1")
    cid, _ = research_claim(migrated, vid, key="c1")
    iid = interpretations.create_interpretation(migrated, vid, statement="r", interpretation_key="i", produced_by="m").interpretation_id
    aid = _assume(migrated, vid)
    assert assumptions.link_claim(migrated, assumption_id=aid, claim_id=cid) is True
    assert assumptions.link_interpretation(migrated, assumption_id=aid, interpretation_id=iid) is True


def test_categorical_only_no_decimal_confidence(migrated):
    vid = ventures.create_venture(migrated, slug="as-cat")
    with pytest.raises(ValueError):
        _assume(migrated, vid, confidence="0.73")
    with pytest.raises(ValueError):
        _assume(migrated, vid, importance="SUPER")


def test_consequence_and_cheapest_test_stored(migrated):
    vid = ventures.create_venture(migrated, slug="as-fields")
    aid = _assume(migrated, vid)
    with migrated.cursor() as cur:
        cur.execute("SELECT consequence_if_false, cheapest_test, importance, confidence FROM assumption WHERE id = %s", (aid,))
        r = cur.fetchone()
    assert r[0] == "thesis collapses" and r[1].startswith("interview") and r[2] == "HIGH" and r[3] == "LOW"


def test_assumption_creates_no_action_or_capital(migrated):
    vid = ventures.create_venture(migrated, slug="as-noeffect")
    _assume(migrated, vid)
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_request")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM capital_entry")
        assert cur.fetchone()[0] == 0


def test_append_only_and_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="as-idem")
    a = _assume(migrated, vid, key="k", proposition="P")
    b = assumptions.create_assumption(migrated, vid, proposition="P", assumption_key="k", importance="LOW",
                                      confidence="LOW", consequence_if_false="c", cheapest_test="t")
    assert b.created is False and b.assumption_id == a
    with pytest.raises(IdempotencyConflictError):
        assumptions.create_assumption(migrated, vid, proposition="DIFFERENT", assumption_key="k", importance="LOW",
                                      confidence="LOW", consequence_if_false="c", cheapest_test="t")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE assumption SET confidence = 'HIGH' WHERE id = %s", (a,))
