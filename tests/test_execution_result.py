"""Raw execution result != canonical success; duplicate results dedupe."""
from __future__ import annotations

from aidan_core import execution

from conftest import setup_action


def test_raw_success_is_not_canonical_success(migrated):
    _vid, aid = setup_action(migrated, slug="er-1", autonomy_level=1, amount=10)
    result_id, created = execution.record_execution_result(
        migrated, aid, external_result_id="e1", reported_outcome="success",
        raw_payload={"token": "whatever"},
    )
    assert created is True and result_id is not None
    # Storing a raw "success" must NOT move the action to canonical success.
    assert execution.get_status(migrated, aid) == "PENDING"


def test_duplicate_external_result_deduped(migrated):
    _vid, aid = setup_action(migrated, slug="er-2", autonomy_level=1, amount=10)
    r1, c1 = execution.record_execution_result(
        migrated, aid, external_result_id="e1", reported_outcome="success"
    )
    r2, c2 = execution.record_execution_result(
        migrated, aid, external_result_id="e1", reported_outcome="success"
    )
    assert c1 is True and c2 is False
    assert r1 == r2
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_result WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 1
