"""Persistence must survive dropping the connection and reconnecting."""
from __future__ import annotations

import os

from aidan_core import audit, migrate

from conftest import MIGRATIONS_DIR


def test_restart_persistence(clean_db):
    migrate.apply(clean_db, MIGRATIONS_DIR)

    with clean_db.cursor() as cur:
        event_id = audit.record_event(
            cur,
            event_type="gate1.slice1.persistence_probe",
            actor="test",
            payload={"k": "v"},
        )
    assert event_id is not None

    # Simulate a process restart: fully close the connection, open a new one.
    url = os.environ["DATABASE_URL"]
    clean_db.close()
    import psycopg

    fresh = psycopg.connect(url, autocommit=True)
    try:
        with fresh.cursor() as cur:
            row = audit.get_event(cur, event_id)
        assert row is not None, "event must survive reconnect"
        assert row[0] == event_id
        assert row[1] == "gate1.slice1.persistence_probe"
        assert row[5] == {"k": "v"}
    finally:
        fresh.close()
