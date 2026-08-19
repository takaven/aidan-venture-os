"""Helpers + deterministic builder workers for the Gate 5 Slice 1 tests.

A builder is an ORDINARY Gate 4 ``WorkerAdapter`` (no DB access, returns a claim
only) — there is no separate builder runtime. ``build_authority`` constructs a
genuine canonical BUILD chain (venture -> opportunity -> recommendation -> BUILD
investment decision -> build ActionRequest) with direct inserts, so build_spec
freezing exercises the real kernel authority guard rather than a mock.
"""
from __future__ import annotations

from collections import namedtuple

from aidan_core.factory.workers import WorkerResult

BuildAuthority = namedtuple(
    "BuildAuthority", "venture_id action_id decision_id recommendation_id opportunity_id"
)


class BuilderWorker:
    """A deterministic builder worker: records its request, returns a claim only."""

    kind = "builder-a"

    def __init__(self, *, reported_outcome="success", structured_output=None, artifacts=(), suffix="1"):
        self._outcome = reported_outcome
        self._out = structured_output or {}
        self._artifacts = tuple(artifacts)
        self._suffix = suffix
        self.calls = 0
        self.last_request = None

    def execute(self, request):  # no connection parameter — no DB authority
        self.calls += 1
        self.last_request = request
        return WorkerResult(
            worker_kind=self.kind,
            external_result_id=f"{self.kind}:{request.action_request_id}:{self._suffix}",
            reported_outcome=self._outcome,
            worker_version="test",
            structured_output=self._out,
            artifacts=self._artifacts,
        )


class BuilderWorkerB(BuilderWorker):
    kind = "builder-b"


_REASON = {"BUILD": "BUILD_CONSIDERATION_READY", "VALIDATE": "VALIDATION_TEST_AVAILABLE"}


def build_authority(
    conn, *, slug, decision="BUILD", autonomy_level=1, required_autonomy=0,
    amount=0, grant=100, key="b",
) -> BuildAuthority:
    """Create a genuine canonical decision chain (BUILD by default). Returns identities."""
    from aidan_core import budget, ventures

    vid = ventures.create_venture(conn, slug=slug, autonomy_level=autonomy_level)
    if grant:
        budget.grant_budget(conn, vid, amount=grant, currency="USD")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO opportunity (venture_id, opportunity_key, buyer_hypothesis, "
            "problem_hypothesis, payload_hash, status) "
            "VALUES (%s, %s, %s, %s, %s, 'CANDIDATE') RETURNING id",
            (vid, f"opp-{key}", "buyer-h", "problem-h", "h"),
        )
        opp_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO next_action_recommendation (venture_id, recommendation_key, opportunity_id, "
            "action_type, dominant_reason_code, input_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (vid, f"rec-{key}", opp_id, decision, _REASON[decision], "h"),
        )
        rec_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO action_request (venture_id, action_type, actor, payload, payload_hash, "
            "idempotency_key, required_autonomy, requested_amount, requested_currency) "
            "VALUES (%s, %s, 'a', '{}'::jsonb, 'h', %s, %s, %s, 'USD') RETURNING id",
            (vid, decision.lower(), f"commit:{key}", required_autonomy, amount),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO investment_decision_record (venture_id, decision, rationale_ref, "
            "resulting_action_id, source_recommendation_id) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (vid, decision, f"next_action_recommendation:{rec_id}", aid, rec_id),
        )
        did = cur.fetchone()[0]
    return BuildAuthority(vid, aid, did, rec_id, opp_id)


# Sensible venture-specific default product intent for a build_spec.
DEFAULT_INTENT = dict(
    buyer="independent physiotherapy clinics",
    problem="manual insurance pre-authorization wastes hours per patient",
    value_proposition="auto-drafts and tracks pre-auth submissions from the clinic calendar",
    product_category="vertical clinic operations tool",
    primary_workflow="calendar -> detect billable session -> draft pre-auth -> track approval",
    differentiators=["payer-specific pre-auth rules engine", "calendar-native trigger"],
    required_capabilities=["preauth_drafting", "approval_tracking"],
    excluded_capabilities=["generic_crm", "generic_chatbot"],
    experience_principles=["one screen per pending pre-auth", "no empty dashboards"],
    expected_output_contract={"require": {"status": "done"}},
)


def freeze_default_build_spec(conn, auth: BuildAuthority, **overrides):
    """Freeze a venture-specific build_spec for a BUILD authority, with overrides."""
    from aidan_core.build import spec as build_spec_mod

    fields = dict(DEFAULT_INTENT)
    fields.update(overrides)
    return build_spec_mod.create_build_spec(
        conn, auth.action_id,
        source_investment_decision_id=auth.decision_id,
        source_recommendation_id=auth.recommendation_id,
        opportunity_id=auth.opportunity_id,
        **fields,
    )
