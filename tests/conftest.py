"""Shared test fixtures for the Gate 1 kernel.

Integration tests require a real PostgreSQL reachable via ``DATABASE_URL``.
When it is absent (e.g. local dev without Postgres), those tests skip cleanly;
pure-logic tests still run. PostgreSQL integration evidence is produced by the
Gate 1 CI workflow's ``postgres:16`` service.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Make the in-repo package importable without an install (local dev).
sys.path.insert(0, str(REPO_ROOT / "packages" / "core"))

# Canonical migrations are a single packaged source (aidan_core/migrations); expose
# their resolved directory for tests that apply migrations explicitly. Resolved via
# the runtime's own default so tests and the installed artifact share one source.
from aidan_core import migrate as _migrate  # noqa: E402

MIGRATIONS_DIR = _migrate.default_migrations_dir()


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


# Objects owned by migrations 0001–0019; dropped for a clean slate per test.
# 0019 adds market_action_spec and ALTERs the execution_spec capability CHECK
# (that constraint drops with execution_spec CASCADE).
# 0020 adds market_observation.
# 0021 adds market_interpretation + market_interpretation_source and an additive
# UNIQUE(id, venture_id) on market_observation (that constraint drops with the table CASCADE).
# 0022 adds recommendation_market_observation and extends the next_action_recommendation
# action_type CHECK (that constraint drops with the table CASCADE).
# 0018 adds deployment_target + release_candidate and ALTERs the execution_spec
# capability CHECK (that constraint drops with execution_spec CASCADE).
# 0017 adds build_quality_evidence + build_quality_assessment.
# 0015 adds build_spec + venture_repository and an additive UNIQUE(id, venture_id)
# on investment_decision_record (that constraint drops with the table CASCADE).
# 0016 adds substrate_release + build_manifest + build_technical_check.
# 0014 only ALTERs execution_attempt (adds failure_class/failure_detail/finished_at);
# the columns drop with the table CASCADE below.
# 0011 only ALTERs investment_decision_record (adds a column, two composite FKs
# and a partial-unique index); dropping that table CASCADE below removes them.
# 0012 adds execution_spec and a success-guard trigger/function on action_request
# (the trigger drops with action_request; the function is dropped explicitly).
# 0013 adds execution_artifact and ALTERs execution_attempt/proof_receipt (dropped
# with their tables CASCADE below).
_DROP_SQL = """
DROP TABLE IF EXISTS market_observation_origin CASCADE;
DROP TABLE IF EXISTS external_evidence_origin CASCADE;
DROP TABLE IF EXISTS recommendation_market_window_completion CASCADE;
DROP TABLE IF EXISTS market_window_completion CASCADE;
DROP TABLE IF EXISTS alpha_intervention CASCADE;
DROP TABLE IF EXISTS recommendation_market_observation CASCADE;
DROP TABLE IF EXISTS market_interpretation_source CASCADE;
DROP TABLE IF EXISTS market_interpretation CASCADE;
DROP TABLE IF EXISTS market_observation CASCADE;
DROP TABLE IF EXISTS market_action_spec CASCADE;
DROP TABLE IF EXISTS release_candidate CASCADE;
DROP TABLE IF EXISTS deployment_target CASCADE;
DROP TABLE IF EXISTS build_quality_assessment CASCADE;
DROP TABLE IF EXISTS build_quality_evidence CASCADE;
DROP TABLE IF EXISTS build_technical_check CASCADE;
DROP TABLE IF EXISTS build_manifest CASCADE;
DROP TABLE IF EXISTS substrate_release CASCADE;
DROP TABLE IF EXISTS build_spec CASCADE;
DROP TABLE IF EXISTS venture_repository CASCADE;
DROP TABLE IF EXISTS execution_artifact CASCADE;
DROP TABLE IF EXISTS execution_spec CASCADE;
DROP TABLE IF EXISTS recommendation_validation_result CASCADE;
DROP TABLE IF EXISTS recommendation_validation_test CASCADE;
DROP TABLE IF EXISTS recommendation_assumption CASCADE;
DROP TABLE IF EXISTS next_action_recommendation CASCADE;
DROP TABLE IF EXISTS validation_result CASCADE;
DROP TABLE IF EXISTS validation_test CASCADE;
DROP TABLE IF EXISTS validation_hypothesis CASCADE;
DROP TABLE IF EXISTS research_question CASCADE;
DROP TABLE IF EXISTS research_run CASCADE;
DROP TABLE IF EXISTS research_result CASCADE;
DROP TABLE IF EXISTS kill_case_dimension_claim CASCADE;
DROP TABLE IF EXISTS kill_case_dimension CASCADE;
DROP TABLE IF EXISTS kill_case CASCADE;
DROP TABLE IF EXISTS opportunity_interpretation CASCADE;
DROP TABLE IF EXISTS opportunity_assumption CASCADE;
DROP TABLE IF EXISTS opportunity_claim CASCADE;
DROP TABLE IF EXISTS opportunity CASCADE;
DROP TABLE IF EXISTS assumption_interpretation CASCADE;
DROP TABLE IF EXISTS assumption_claim CASCADE;
DROP TABLE IF EXISTS assumption CASCADE;
DROP TABLE IF EXISTS interpretation_claim CASCADE;
DROP TABLE IF EXISTS interpretation CASCADE;
DROP TABLE IF EXISTS claim_evidence CASCADE;
DROP TABLE IF EXISTS observation CASCADE;
DROP TABLE IF EXISTS claim CASCADE;
DROP TABLE IF EXISTS source_receipt CASCADE;
DROP TABLE IF EXISTS proof_receipt CASCADE;
DROP TABLE IF EXISTS execution_result CASCADE;
DROP TABLE IF EXISTS execution_attempt CASCADE;
DROP TABLE IF EXISTS evidence_record CASCADE;
DROP TABLE IF EXISTS approval CASCADE;
DROP TABLE IF EXISTS capital_entry CASCADE;
DROP TABLE IF EXISTS budget_account CASCADE;
DROP TABLE IF EXISTS policy_decision CASCADE;
DROP TABLE IF EXISTS kill_switch CASCADE;
DROP TABLE IF EXISTS investment_decision_record CASCADE;
DROP TABLE IF EXISTS action_request CASCADE;
DROP TABLE IF EXISTS venture_mandate_version CASCADE;
DROP TABLE IF EXISTS venture CASCADE;
DROP TABLE IF EXISTS audit_event CASCADE;
DROP TABLE IF EXISTS schema_migrations CASCADE;
DROP TYPE IF EXISTS lifecycle_state CASCADE;
DROP TYPE IF EXISTS run_status CASCADE;
DROP TYPE IF EXISTS investment_decision CASCADE;
DROP TYPE IF EXISTS policy_decision_kind CASCADE;
DROP FUNCTION IF EXISTS audit_event_immutable() CASCADE;
DROP FUNCTION IF EXISTS append_only_guard() CASCADE;
DROP FUNCTION IF EXISTS approval_terminal_guard() CASCADE;
DROP FUNCTION IF EXISTS opportunity_content_immutable() CASCADE;
DROP FUNCTION IF EXISTS research_run_guard() CASCADE;
DROP FUNCTION IF EXISTS validation_test_guard() CASCADE;
DROP FUNCTION IF EXISTS action_request_success_guard() CASCADE;
"""


@pytest.fixture
def conn():
    """An autocommit connection to the canonical test database, or skip."""
    url = database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; PostgreSQL integration runs in CI")
    connection = __import__("psycopg").connect(url, autocommit=True)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def clean_db(conn):
    """Drop all kernel objects so a test starts from an empty schema."""
    with conn.cursor() as cur:
        cur.execute(_DROP_SQL)
    return conn


@pytest.fixture
def migrated(clean_db):
    """A clean database with all migrations applied."""
    from aidan_core import migrate

    migrate.apply(clean_db)   # default: canonical migrations shipped as package resources
    return clean_db


def setup_action(
    conn,
    *,
    slug,
    amount=10,
    autonomy_level=1,
    required_autonomy=0,
    grant=100,
    key="k",
    currency="USD",
):
    """Create venture + grant + action; return (venture_id, action_id)."""
    from aidan_core import actions, budget, ventures

    vid = ventures.create_venture(conn, slug=slug, autonomy_level=autonomy_level)
    if grant:
        budget.grant_budget(conn, vid, amount=grant, currency=currency)
    aid = actions.submit_action_request(
        conn, venture_id=vid, action_type="spend", actor="a", idempotency_key=key,
        required_autonomy=required_autonomy, requested_amount=amount, requested_currency=currency,
    ).action_id
    return vid, aid


def research_claim(conn, vid, *, statement="claim", key="c", stance=None):
    """Build source -> observation -> claim; optionally attach one stance. Returns (claim_id, observation_id)."""
    from datetime import datetime, timezone

    from aidan_core.research import claims, observations, sources
    from aidan_core.research.adapters import AcquiredSource

    src = sources.ingest(conn, vid, AcquiredSource(
        locator="L", source_type="WEB_PAGE", content=f"body-{key}",
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), retrieved_by="a", acquisition_key=f"src-{key}",
    )).evidence_record_id
    obs = observations.create_observation(
        conn, vid, source_evidence_id=src, statement=f"obs-{key}", observation_key=f"obs-{key}"
    ).evidence_record_id
    cid = claims.create_claim(conn, vid, statement=statement, claim_key=key).evidence_record_id
    if stance in ("SUPPORTS", "CONTRADICTS"):
        claims.link_evidence(conn, claim_id=cid, observation_id=obs, stance=stance)
    return cid, obs


def full_kill_case(conn, opportunity_id, *, key="kc", assessment="MATERIAL_RISK"):
    """Create a Kill Case and assess all required dimensions. Returns kill_case_id."""
    from aidan_core.research import killcase

    kc = killcase.create_kill_case(
        conn, opportunity_id=opportunity_id, kill_case_key=key, disposition="PROCEED_WITH_RISKS"
    ).kill_case_id
    for dim in killcase.REQUIRED_DIMENSIONS:
        killcase.add_dimension(conn, kill_case_id=kc, dimension=dim, assessment=assessment, rationale="r")
    return kc
