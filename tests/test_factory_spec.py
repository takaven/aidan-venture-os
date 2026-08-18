"""Gate 4 Slice 1 — immutable, venture-consistent, 1:1 execution specification."""
from __future__ import annotations

import psycopg
import pytest

from aidan_core import ventures
from aidan_core.errors import IdempotencyConflictError, NotFoundError
from aidan_core.factory import spec as spec_mod

from conftest import setup_action


def _spec(conn, aid, **over):
    kw = dict(
        worker_kind="fake-a", verifier_kind="token-match-v1", timeout_seconds=60,
        max_attempts=3, capability_scope=["READ_REPOSITORY"],
        task_payload={"goal": "x"}, expected_output_contract={"kind": "structured"},
    )
    kw.update(over)
    return spec_mod.create_execution_spec(conn, aid, **kw)


def test_spec_is_one_per_action_and_idempotent(migrated):
    _vid, aid = setup_action(migrated, slug="fs-1")
    a = _spec(migrated, aid)
    b = _spec(migrated, aid)
    assert a.created is True and b.created is False
    assert a.spec_id == b.spec_id and a.spec_hash == b.spec_hash
    with migrated.cursor() as cur:
        cur.execute("SELECT count(*) FROM execution_spec WHERE action_request_id = %s", (aid,))
        assert cur.fetchone()[0] == 1


def test_spec_changed_content_conflicts(migrated):
    _vid, aid = setup_action(migrated, slug="fs-2")
    _spec(migrated, aid, task_payload={"goal": "x"})
    with pytest.raises(IdempotencyConflictError):
        _spec(migrated, aid, task_payload={"goal": "DIFFERENT"})


def test_spec_requires_existing_action(migrated):
    with pytest.raises(NotFoundError):
        _spec(migrated, "00000000-0000-0000-0000-000000000000")


def test_spec_cross_venture_rejected_by_db(migrated):
    _vidA, aid = setup_action(migrated, slug="fs-a")
    vidB = ventures.create_venture(migrated, slug="fs-b")
    # A raw insert claiming action A belongs to venture B violates the composite FK.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO execution_spec (action_request_id, venture_id, worker_kind, task_hash, "
                "expected_output_contract, verifier_kind, timeout_seconds, max_attempts, spec_hash) "
                "VALUES (%s, %s, 'w', 'h', '{}'::jsonb, 'v', 60, 3, 's')",
                (aid, vidB),
            )


def test_spec_is_immutable(migrated):
    _vid, aid = setup_action(migrated, slug="fs-imm")
    _spec(migrated, aid)
    for col, val in (
        ("worker_kind", "'other'"), ("verifier_kind", "'other'"),
        ("timeout_seconds", "999"), ("max_attempts", "99"),
        ("task_payload", "'{\"goal\":\"drift\"}'::jsonb"),
        ("capability_scope", "ARRAY['PRODUCE_PATCH']::text[]"),
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            with migrated.cursor() as cur:
                cur.execute(f"UPDATE execution_spec SET {col} = {val} WHERE action_request_id = %s", (aid,))
    # Original unchanged.
    row = spec_mod.get_execution_spec(migrated, aid)
    assert row[3] == "fake-a" and row[8] == 60 and list(row[10]) == ["READ_REPOSITORY"]


def test_unknown_capability_rejected_in_api(migrated):
    _vid, aid = setup_action(migrated, slug="fs-cap")
    with pytest.raises(ValueError):
        _spec(migrated, aid, capability_scope=["DEPLOY_TO_PROD"])


def test_unknown_capability_rejected_in_db(migrated):
    _vid, aid = setup_action(migrated, slug="fs-cap2")
    with pytest.raises(psycopg.errors.CheckViolation):
        with migrated.cursor() as cur:
            cur.execute(
                "INSERT INTO execution_spec (action_request_id, venture_id, worker_kind, task_hash, "
                "expected_output_contract, verifier_kind, timeout_seconds, max_attempts, capability_scope, spec_hash) "
                "SELECT %s, venture_id, 'w', 'h', '{}'::jsonb, 'v', 60, 3, ARRAY['DEPLOY']::text[], 's' "
                "FROM action_request WHERE id = %s",
                (aid, aid),
            )


def test_spec_hash_is_deterministic():
    kw = dict(
        worker_kind="w", task_payload={"a": 1, "b": 2}, expected_output_contract={"k": "v"},
        verifier_kind="v", timeout_seconds=30, max_attempts=2, capability_scope=["RUN_TESTS", "READ_REPOSITORY"],
    )
    h1 = spec_mod.compute_spec_hash(**kw)
    kw2 = dict(kw, capability_scope=["READ_REPOSITORY", "RUN_TESTS"])  # order must not matter
    h2 = spec_mod.compute_spec_hash(**kw2)
    assert h1 == h2
    h3 = spec_mod.compute_spec_hash(**dict(kw, timeout_seconds=31))
    assert h3 != h1
