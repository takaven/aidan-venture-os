"""Observations: canonical envelope, source provenance, idempotency, append-only.

Note on verification honesty: Slice 1 retains only a bounded excerpt + content
hash, so these tests verify provenance LINK integrity only. Textual source
verification (that the observed statement occurs in the full source) is NOT
claimed or tested.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import IdempotencyConflictError, NotFoundError
from aidan_core.research import observations, sources
from aidan_core.research.adapters import AcquiredSource

UTC = timezone.utc


def _source(conn, vid, *, key="acq", content="c", locator="L"):
    return sources.ingest(conn, vid, AcquiredSource(
        locator=locator, source_type="WEB_PAGE", content=content,
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), retrieved_by="a", acquisition_key=key,
    )).evidence_record_id


def test_observation_envelope_and_provenance(migrated):
    vid = ventures.create_venture(migrated, slug="ob-1")
    src = _source(migrated, vid)
    res = observations.create_observation(
        migrated, vid, source_evidence_id=src, statement="revenue grew 20%", observation_key="o1"
    )
    assert res.created is True
    row = observations.get_observation(migrated, res.evidence_record_id)
    assert row[1] == vid and row[2] == src and row[4] == "revenue grew 20%" and row[8] == "OBSERVATION"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM evidence_record WHERE venture_id = %s AND kind = 'OBSERVATION'", (vid,))
        assert cur.fetchone()[0] == 1


def test_observation_requires_existing_source(migrated):
    vid = ventures.create_venture(migrated, slug="ob-nosrc")
    with pytest.raises(NotFoundError):
        observations.create_observation(
            migrated, vid, source_evidence_id="00000000-0000-0000-0000-000000000000",
            statement="x", observation_key="o1",
        )


def test_observation_wrong_kind_envelope_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="ob-kind")
    src = _source(migrated, vid)
    with migrated.cursor() as cur:
        cur.execute("INSERT INTO evidence_record (venture_id, kind, content_hash) VALUES (%s,'CLAIM','h') RETURNING id", (vid,))
        claim_env = cur.fetchone()[0]
    # observation subtype pointed at a CLAIM envelope -> envelope FK (kind=OBSERVATION) fails.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO observation (evidence_record_id, venture_id, source_evidence_id, statement, observation_key) "
                "VALUES (%s, %s, %s, 's', 'k')",
                (claim_env, vid, src),
            )


def test_observation_subtype_without_envelope_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="ob-noenv")
    src = _source(migrated, vid)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO observation (evidence_record_id, venture_id, source_evidence_id, statement, observation_key) "
                "VALUES (gen_random_uuid(), %s, %s, 's', 'k')",
                (vid, src),
            )


def test_observation_append_only(migrated):
    vid = ventures.create_venture(migrated, slug="ob-imm")
    src = _source(migrated, vid)
    res = observations.create_observation(migrated, vid, source_evidence_id=src, statement="s", observation_key="o1")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE observation SET statement = 'x' WHERE evidence_record_id = %s", (res.evidence_record_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM observation WHERE evidence_record_id = %s", (res.evidence_record_id,))


def test_observation_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="ob-idem")
    src = _source(migrated, vid)
    a = observations.create_observation(migrated, vid, source_evidence_id=src, statement="s", observation_key="k")
    b = observations.create_observation(migrated, vid, source_evidence_id=src, statement="s", observation_key="k")
    assert b.created is False and b.evidence_record_id == a.evidence_record_id
    with pytest.raises(IdempotencyConflictError):
        observations.create_observation(migrated, vid, source_evidence_id=src, statement="DIFFERENT", observation_key="k")
    # Two distinct observations from the same source persist.
    c = observations.create_observation(migrated, vid, source_evidence_id=src, statement="another", observation_key="k2")
    assert c.created is True and c.evidence_record_id != a.evidence_record_id
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM observation WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM audit_event WHERE venture_id = %s AND event_type = 'evidence.observation_created'", (vid,))
        assert cur.fetchone()[0] == 2  # retry emitted no duplicate event


def test_provenance_is_link_not_semantic(migrated):
    # We do NOT verify the statement occurs in the source text; only the link.
    vid = ventures.create_venture(migrated, slug="ob-linkonly")
    src = _source(migrated, vid, content="totally unrelated source body")
    res = observations.create_observation(
        migrated, vid, source_evidence_id=src, statement="a claim-like statement not in the source",
        observation_key="o1",
    )
    assert res.created is True  # link integrity only; textual verification not claimed
