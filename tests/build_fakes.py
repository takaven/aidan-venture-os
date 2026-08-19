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

from factory_fakes import registry_with

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


# ---- Slice 2 helpers ---------------------------------------------------------------
_TECH_CONTRACT = {"required_files": ["app/main.py"], "forbidden_files": [], "required_commands": ["pytest"]}
_CAPS2 = ["READ_REPOSITORY", "WRITE_ISOLATED_WORKSPACE", "PRODUCE_PATCH"]
GOOD_CANDIDATE = [{"path": "app/main.py", "content": "def run():\n    return 'clinic-preauth'\n"}]


class ScriptedBuilderWorker(BuilderWorker):
    """Returns a different structured_output per attempt (drives retry-then-correct)."""

    def __init__(self, outputs):
        super().__init__()
        self._outputs = list(outputs)

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        out = self._outputs[min(self.calls - 1, len(self._outputs) - 1)]
        return WorkerResult(
            worker_kind=self.kind, external_result_id=f"{self.kind}:{request.action_request_id}:{self.calls}",
            reported_outcome="success", worker_version="test", structured_output=out,
        )


def make_substrate(conn, *, key="rel", sha="sha-0016", components=None):
    from aidan_core.build import substrate as S

    return S.create_substrate_release(
        conn, release_key=key, source_sha=sha,
        components=components or ["CONFIG_BOUNDARY", "TEST_HARNESS"],
    )


# A candidate product descriptor that satisfies the DEFAULT_INTENT build_spec.
GOOD_PRODUCT_MANIFEST = {
    "buyer": "independent physiotherapy clinics",
    "workflows": ["calendar -> detect billable session -> draft pre-auth -> track approval"],
    "features": ["preauth_drafting", "approval_tracking"],
    "differentiators_implemented": ["payer-specific pre-auth rules engine", "calendar-native trigger"],
    "vocabulary": ["pre-auth", "payer", "clinic", "billable session"],
    "states": ["empty", "loading", "error"],
    "cta": ["submit_preauth"],
    "dead_ends": [],
}


def captured_manifest(conn, slug, *, key="q", extra_contract=None, candidate_files=None):
    """Full Slice-1+2 path: freeze build_spec, register repo, dispatch, capture manifest
    (auto workspace) + Technical checks. Returns (auth, build_manifest_id)."""
    from aidan_core.build import repository as repo_mod
    from aidan_core.build import runtime as build_runtime

    rel = make_substrate(conn, key=f"rel-{key}")
    contract = {"require": {"status": "done"}, "technical": _TECH_CONTRACT}
    if extra_contract:
        contract.update(extra_contract)
    auth = build_authority(conn, slug=slug, key=key)
    freeze_default_build_spec(conn, auth, expected_output_contract=contract)
    repo_mod.register_venture_repository(conn, auth.venture_id, repository_ref=f"venture://{slug}/app")
    worker = BuilderWorker(structured_output={
        "status": "done", "candidate_files": candidate_files or GOOD_CANDIDATE})
    build_runtime.execute_build(
        conn, auth.action_id, registry=registry_with(worker), worker_kind=worker.kind,
        verifier_kind="structured-contract", capability_scope=_CAPS2, timeout_seconds=60, max_attempts=1)
    out = build_runtime.capture_and_check_build(
        conn, auth.action_id, substrate_release_id=rel.substrate_release_id)
    return auth, out["manifest"].build_manifest_id


def dispatched_build(conn, slug, *, candidate_files=None, key="b", technical="default",
                     status="done", worker=None, max_attempts=1, register_repo=True):
    """Full Slice-1 authority + dispatch a builder that declares candidate files."""
    from aidan_core.build import repository as repo_mod
    from aidan_core.build import runtime as build_runtime

    auth = build_authority(conn, slug=slug, key=key)
    tech = _TECH_CONTRACT if technical == "default" else technical
    contract = {"require": {"status": "done"}}
    if tech is not None:
        contract["technical"] = tech
    freeze_default_build_spec(conn, auth, expected_output_contract=contract)
    if register_repo:
        repo_mod.register_venture_repository(conn, auth.venture_id, repository_ref=f"venture://{slug}/app")
    if worker is None:
        so = {"status": status, "candidate_files": candidate_files if candidate_files is not None else GOOD_CANDIDATE}
        worker = BuilderWorker(structured_output=so)
    dispatch, result = build_runtime.execute_build(
        conn, auth.action_id, registry=registry_with(worker), worker_kind=worker.kind,
        verifier_kind="structured-contract", capability_scope=_CAPS2,
        timeout_seconds=60, max_attempts=max_attempts,
    )
    return auth, worker, dispatch, result
