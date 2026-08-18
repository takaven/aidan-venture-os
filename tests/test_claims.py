"""Claims: envelope, derived state, explicit stances, idempotency, integrity."""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import EvidenceRelationConflictError, IdempotencyConflictError
from aidan_core.research import claims, observations, sources
from aidan_core.research.adapters import AcquiredSource

UTC = timezone.utc


def _obs(conn, vid, *, statement, key, src_key=None, content="c"):
    src = sources.ingest(conn, vid, AcquiredSource(
        locator="L", source_type="WEB_PAGE", content=content, retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        retrieved_by="a", acquisition_key=src_key or f"src-{key}",
    )).evidence_record_id
    return observations.create_observation(conn, vid, source_evidence_id=src, statement=statement, observation_key=key).evidence_record_id


def _claim(conn, vid, *, statement="claim", key="c1"):
    return claims.create_claim(conn, vid, statement=statement, claim_key=key).evidence_record_id


def test_claim_envelope(migrated):
    vid = ventures.create_venture(migrated, slug="cl-1")
    cid = _claim(migrated, vid)
    with migrated.cursor() as cur:
        cur.execute("SELECT kind FROM evidence_record WHERE id = %s", (cid,))
        assert cur.fetchone()[0] == "CLAIM"
        cur.execute("SELECT count(*) FROM claim WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 1


def test_claim_wrong_kind_envelope_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="cl-kind")
    with migrated.cursor() as cur:
        cur.execute("INSERT INTO evidence_record (venture_id, kind, content_hash) VALUES (%s,'SOURCE','h') RETURNING id", (vid,))
        src_env = cur.fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute("INSERT INTO claim (evidence_record_id, venture_id, statement, claim_key) VALUES (%s,%s,'s','k')", (src_env, vid))


def test_unsupported_and_no_caller_set_state(migrated):
    vid = ventures.create_venture(migrated, slug="cl-unsup")
    cid = _claim(migrated, vid)
    assert claims.claim_state(migrated, cid) == "UNSUPPORTED"
    # No API lets a caller assert claim truth directly.
    assert not hasattr(claims, "set_claim_state")
    assert not hasattr(claims, "mark_supported")


def test_supported(migrated):
    vid = ventures.create_venture(migrated, slug="cl-sup")
    cid = _claim(migrated, vid)
    obs = _obs(migrated, vid, statement="supports", key="o1")
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="SUPPORTS")
    assert claims.claim_state(migrated, cid) == "SUPPORTED"


def test_contradicted(migrated):
    vid = ventures.create_venture(migrated, slug="cl-con")
    cid = _claim(migrated, vid)
    obs = _obs(migrated, vid, statement="contradicts", key="o1")
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "CONTRADICTED"


def test_disputed_preserves_both(migrated):
    vid = ventures.create_venture(migrated, slug="cl-disp")
    cid = _claim(migrated, vid)
    a = _obs(migrated, vid, statement="supports", key="oa")
    b = _obs(migrated, vid, statement="contradicts", key="ob")
    claims.link_evidence(migrated, claim_id=cid, observation_id=a, stance="SUPPORTS")
    claims.link_evidence(migrated, claim_id=cid, observation_id=b, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "DISPUTED"
    # Both observations and both sources persist; no evidence mutated.
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM observation WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM source_receipt WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM claim_evidence WHERE claim_id = %s", (cid,))
        assert cur.fetchone()[0] == 2


def test_later_contradiction_does_not_mutate_old_evidence(migrated):
    vid = ventures.create_venture(migrated, slug="cl-later")
    cid = _claim(migrated, vid)
    a = _obs(migrated, vid, statement="supports", key="oa")
    claims.link_evidence(migrated, claim_id=cid, observation_id=a, stance="SUPPORTS")
    assert claims.claim_state(migrated, cid) == "SUPPORTED"
    b = _obs(migrated, vid, statement="contradicts", key="ob")
    claims.link_evidence(migrated, claim_id=cid, observation_id=b, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "DISPUTED"
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM claim_evidence WHERE claim_id = %s AND stance = 'SUPPORTS'", (cid,))
        assert cur.fetchone()[0] == 1  # original support remains


def test_multiple_contradictions_no_winner(migrated):
    vid = ventures.create_venture(migrated, slug="cl-multi")
    cid = _claim(migrated, vid)
    for i in range(3):
        obs = _obs(migrated, vid, statement=f"contradiction {i}", key=f"oc{i}")
        claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="CONTRADICTS")
    assert claims.claim_state(migrated, cid) == "CONTRADICTED"
    prov = claims.provenance(migrated, cid)
    assert len(prov["paths"]) == 3 and all(p["stance"] == "CONTRADICTS" for p in prov["paths"])


def test_relation_integrity(migrated):
    a = ventures.create_venture(migrated, slug="cl-vA")
    b = ventures.create_venture(migrated, slug="cl-vB")
    claim_a = _claim(migrated, a, key="ca")
    obs_b = _obs(migrated, b, statement="s", key="ob")
    # Cross-venture relation rejected by the observation-side composite FK.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        claims.link_evidence(migrated, claim_id=claim_a, observation_id=obs_b, stance="SUPPORTS")
    # Claim -> non-observation evidence rejected (a SOURCE id is not an observation).
    src = sources.ingest(migrated, a, AcquiredSource(
        locator="L", source_type="WEB_PAGE", content="c", retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        retrieved_by="a", acquisition_key="src-x")).evidence_record_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        claims.link_evidence(migrated, claim_id=claim_a, observation_id=src, stance="SUPPORTS")


def test_relation_idempotency_and_opposite_stance_conflict(migrated):
    vid = ventures.create_venture(migrated, slug="cl-relidem")
    cid = _claim(migrated, vid)
    obs = _obs(migrated, vid, statement="s", key="o1")
    r1 = claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="SUPPORTS")
    r2 = claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="SUPPORTS")
    assert r1.created is True and r2.created is False
    with pytest.raises(EvidenceRelationConflictError):
        claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="CONTRADICTS")
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM claim_evidence WHERE claim_id = %s", (cid,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM audit_event WHERE venture_id = %s AND event_type = 'evidence.claim_supported'", (vid,))
        assert cur.fetchone()[0] == 1


def test_claim_idempotency(migrated):
    vid = ventures.create_venture(migrated, slug="cl-idem")
    a = claims.create_claim(migrated, vid, statement="S", claim_key="k")
    b = claims.create_claim(migrated, vid, statement="S", claim_key="k")
    assert b.created is False and b.evidence_record_id == a.evidence_record_id
    with pytest.raises(IdempotencyConflictError):
        claims.create_claim(migrated, vid, statement="DIFFERENT", claim_key="k")


def test_relation_append_only(migrated):
    vid = ventures.create_venture(migrated, slug="cl-relimm")
    cid = _claim(migrated, vid)
    obs = _obs(migrated, vid, statement="s", key="o1")
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="SUPPORTS")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE claim_evidence SET stance = 'CONTRADICTS' WHERE claim_id = %s", (cid,))
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM claim_evidence WHERE claim_id = %s", (cid,))
