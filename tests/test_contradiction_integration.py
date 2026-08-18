"""Slice 2 core invariant: contradictory evidence coexists and survives restart."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg

from aidan_core import ventures
from aidan_core.research import claims, observations, sources
from aidan_core.research.adapters import AcquiredSource

UTC = timezone.utc


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _source(conn, vid, *, locator, key, content, published, retrieved):
    return sources.ingest(conn, vid, AcquiredSource(
        locator=locator, source_type="WEB_PAGE", content=content, retrieved_at=retrieved,
        retrieved_by="adapter.alpha", acquisition_key=key,
        published_at=published, publication_time_known=True,
    )).evidence_record_id


def test_contradiction_provenance_end_to_end(migrated):
    # migrated ensures a clean, migrated schema; own connections simulate restart.
    c = _connect()
    try:
        vid = ventures.create_venture(c, slug="ci-e2e")                                     # 1
        src_a = _source(c, vid, locator="https://a", key="A", content="A body",
                        published=datetime(2025, 1, 1, tzinfo=UTC), retrieved=datetime(2026, 1, 1, tzinfo=UTC))  # 2
        src_b = _source(c, vid, locator="https://b", key="B", content="B body",
                        published=datetime(2026, 5, 20, tzinfo=UTC), retrieved=datetime(2026, 6, 1, tzinfo=UTC))  # 3
        obs_a = observations.create_observation(c, vid, source_evidence_id=src_a, statement="SMB WTP strong", observation_key="oa").evidence_record_id  # 4
        obs_b = observations.create_observation(c, vid, source_evidence_id=src_b, statement="SMB WTP weak", observation_key="ob").evidence_record_id    # 5
        cid = claims.create_claim(c, vid, statement="SMB accounting teams have strong WTP", claim_key="c1").evidence_record_id  # 6
        claims.link_evidence(c, claim_id=cid, observation_id=obs_a, stance="SUPPORTS")       # 7
        assert claims.claim_state(c, cid) == "SUPPORTED"                                     # 8
        claims.link_evidence(c, claim_id=cid, observation_id=obs_b, stance="CONTRADICTS")    # 9
        assert claims.claim_state(c, cid) == "DISPUTED"                                      # 10
        prov = claims.provenance(c, cid)                                                     # 11
        assert {p["stance"] for p in prov["paths"]} == {"SUPPORTS", "CONTRADICTS"}
        assert {p["source_evidence_id"] for p in prov["paths"]} == {src_a, src_b}
        with c.cursor() as cur:                                                              # 12
            for table, n in (("source_receipt", 2), ("observation", 2)):
                cur.execute(f"SELECT count(*) FROM {table} WHERE venture_id = %s", (vid,))
                assert cur.fetchone()[0] == n
            cur.execute("SELECT count(*) FROM claim_evidence WHERE claim_id = %s", (cid,))
            assert cur.fetchone()[0] == 2
    finally:
        c.close()                                                                           # 13 restart

    c = _connect()
    try:
        assert claims.claim_state(c, cid) == "DISPUTED"                                      # 14
        assert len(claims.provenance(c, cid)["paths"]) == 2
        # Freshness qualifies paths but never rewrites structural state.
        as_of = datetime(2026, 6, 1, tzinfo=UTC)
        f_support = sources.evaluate_freshness(published_at=datetime(2025, 1, 1, tzinfo=UTC), publication_time_known=True, as_of=as_of, max_age=timedelta(days=30))
        f_contra = sources.evaluate_freshness(published_at=datetime(2026, 5, 20, tzinfo=UTC), publication_time_known=True, as_of=as_of, max_age=timedelta(days=30))
        assert f_support == "STALE" and f_contra == "CURRENT"
        assert claims.claim_state(c, cid) == "DISPUTED"
    finally:
        c.close()


def test_research_writes_have_no_governance_authority(migrated):
    vid = ventures.create_venture(migrated, slug="ci-sec")
    src = _source(migrated, vid, locator="L", key="k", content="c",
                  published=datetime(2026, 1, 1, tzinfo=UTC), retrieved=datetime(2026, 1, 1, tzinfo=UTC))
    obs = observations.create_observation(migrated, vid, source_evidence_id=src, statement="s", observation_key="o").evidence_record_id
    cid = claims.create_claim(migrated, vid, statement="p", claim_key="c").evidence_record_id
    claims.link_evidence(migrated, claim_id=cid, observation_id=obs, stance="SUPPORTS")
    # No governance / capital / action state was touched by research writes.
    assert ventures.get_venture(migrated, vid)[2] == "DISCOVERED"
    with migrated.cursor() as cur:
        for table in ("policy_decision", "action_request", "kill_switch", "proof_receipt"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM budget_account WHERE venture_id = %s", (vid,))
        assert cur.fetchone()[0] == 0
