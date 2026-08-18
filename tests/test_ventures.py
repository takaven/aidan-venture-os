"""Venture creation and append-only mandate versions."""
from __future__ import annotations

import os

import psycopg
import pytest

from aidan_core import ventures


def test_create_venture(migrated):
    vid = ventures.create_venture(migrated, slug="alpha", autonomy_level=1)
    row = ventures.get_venture(migrated, vid)
    assert row is not None
    assert row[1] == "alpha"
    assert row[2] == "DISCOVERED"  # lifecycle_state default
    assert row[4] == 1  # autonomy_level


def test_duplicate_slug_rejected(migrated):
    ventures.create_venture(migrated, slug="dup")
    with pytest.raises(psycopg.errors.UniqueViolation):
        ventures.create_venture(migrated, slug="dup")


def test_venture_survives_reconnect(migrated):
    vid = ventures.create_venture(migrated, slug="persist")
    url = os.environ["DATABASE_URL"]
    migrated.close()
    fresh = psycopg.connect(url, autocommit=True)
    try:
        assert ventures.get_venture(fresh, vid) is not None
    finally:
        fresh.close()


def test_mandate_versions_append_and_current(migrated):
    vid = ventures.create_venture(migrated, slug="mandate")
    v1 = ventures.append_mandate_version(migrated, vid, content_hash="h1")
    v2 = ventures.append_mandate_version(migrated, vid, content_hash="h2")
    assert (v1, v2) == (1, 2)
    current = ventures.get_current_mandate_version(migrated, vid)
    assert current[2] == 2 and current[3] == "h2"
    # venture.mandate_version designates the current version.
    assert ventures.get_venture(migrated, vid)[3] == 2


def test_duplicate_mandate_version_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="dupver")
    ventures.append_mandate_version(migrated, vid, content_hash="h1")
    # Force an explicit duplicate (venture_id, version) — unique constraint must reject.
    with pytest.raises(psycopg.errors.UniqueViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO venture_mandate_version (venture_id, version, content_hash) "
                "VALUES (%s, %s, %s)",
                (vid, 1, "h-dup"),
            )


def test_mandate_version_update_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="noupd")
    ventures.append_mandate_version(migrated, vid, content_hash="h1")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute(
                "UPDATE venture_mandate_version SET content_hash = 'x' WHERE venture_id = %s",
                (vid,),
            )


def test_mandate_version_delete_rejected(migrated):
    vid = ventures.create_venture(migrated, slug="nodel")
    ventures.append_mandate_version(migrated, vid, content_hash="h1")
    with pytest.raises(psycopg.errors.RaiseException):
        with migrated.cursor() as cur:
            cur.execute(
                "DELETE FROM venture_mandate_version WHERE venture_id = %s", (vid,)
            )
    # Still present.
    assert ventures.get_current_mandate_version(migrated, vid) is not None
