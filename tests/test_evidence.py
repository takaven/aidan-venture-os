"""Evidence Ledger primitive: append-only."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import evidence, ventures


def test_append_and_read(migrated):
    vid = ventures.create_venture(migrated, slug="ev-1")
    eid = evidence.record_evidence(migrated, vid, kind="SOURCE", content_hash="h", source_ref="url")
    row = evidence.get_evidence(migrated, eid)
    assert row[3] == "SOURCE" and row[5] == "h"


def test_update_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="ev-2")
    eid = evidence.record_evidence(migrated, vid, kind="OBSERVATION", content_hash="h")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("UPDATE evidence_record SET content_hash = 'x' WHERE id = %s", (eid,))


def test_delete_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="ev-3")
    eid = evidence.record_evidence(migrated, vid, kind="CLAIM", content_hash="h")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute("DELETE FROM evidence_record WHERE id = %s", (eid,))
    assert evidence.get_evidence(migrated, eid) is not None
