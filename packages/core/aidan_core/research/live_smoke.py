"""Bounded Gate-8 live research smoke entrypoint (manual, workflow_dispatch only).

Runs EXACTLY ONE governed research run against the REAL Tavily + Anthropic providers using a fresh
migrated PostgreSQL, enforcing hard bounds (<=2 Tavily calls, <=2 Anthropic calls; the Anthropic
``max_tokens`` cap keeps total provider spend well under the US$1.00 authorized ceiling for any
Claude model at these bounds). It composes existing production APIs only — it changes NO kernel
behaviour, writes NO migration, and performs NO deploy / Postmark / email / market action.

Emits a single sanitized JSON evidence line to stdout and a short human summary; it NEVER prints
provider keys. It fails closed: unconfigured or any provider failure/refusal/malformed response
produces no fabricated evidence and no false success (non-zero exit), never a repaired run.

Exit codes: 0 PASS · 2 CONFIG (missing DATABASE_URL or provider config) · 3 PROVIDER_FAILED_CLOSED
· 4 ACCEPTANCE_FAILED (bounds/governance/secret/outcome check failed).
"""
from __future__ import annotations

import json
import os
import sys

from ..errors import ConfigError, InvalidAcquisitionError
from . import http_providers as hp

_MAX_TAVILY = 2
_MAX_ANTHROPIC = 2
_SPEND_CEILING_USD = 1.00
_REQUIRED = ("RESEARCH_TAVILY_API_KEY", "RESEARCH_ANTHROPIC_API_KEY", "RESEARCH_ANTHROPIC_MODEL")
_MANDATE = (
    "MANDATE: Build a venture that removes the burden of costly, error-prone manual financial "
    "reconciliation for small and mid-sized business finance and operations teams.")
_LEGIT_OUTCOMES = ("OPPORTUNITIES_FOUND", "NO_CREDIBLE_OPPORTUNITY", "INSUFFICIENT_EVIDENCE")


class BoundedTavily(hp.TavilySearchAdapter):
    """TavilySearchAdapter that counts and hard-bounds live acquisition calls, failing closed on the
    over-bound call BEFORE it reaches the network. ``counter`` is a shared ``{"tavily","anthropic"}`` dict."""

    def __init__(self, counter: dict, **kw):
        super().__init__(**kw)
        self._counter = counter

    def acquire(self, query):
        self._counter["tavily"] += 1
        if self._counter["tavily"] > _MAX_TAVILY:
            raise InvalidAcquisitionError("tavily call bound exceeded")   # before super().acquire's transport
        return super().acquire(query)


class BoundedAnthropic(hp.AnthropicResearchProposer):
    """AnthropicResearchProposer that counts and hard-bounds live Messages calls at the REAL network
    boundary ``_tool_call`` — so BOTH research_questions() and propose() are counted, and an over-bound
    call fails closed before transport."""

    def __init__(self, counter: dict, **kw):
        super().__init__(**kw)
        self._counter = counter

    def _tool_call(self, prompt, tool):
        self._counter["anthropic"] += 1
        if self._counter["anthropic"] > _MAX_ANTHROPIC:
            exc = InvalidAcquisitionError("anthropic call bound exceeded")   # before super()._tool_call
            exc.provider_failure_code = "ANTHROPIC_CALL_BOUND"
            raise exc
        return super()._tool_call(prompt, tool)


def _emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True))


def _facts(vid, counter, *, outcome, run=None) -> dict:
    import psycopg

    fresh = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)  # fresh connection: durable state
    try:
        def c(sql, *a):
            with fresh.cursor() as cur:
                cur.execute(sql, a)
                return cur.fetchone()[0]

        governance = (
            c("SELECT count(*) FROM investment_decision_record WHERE venture_id=%s", vid)
            + c("SELECT count(*) FROM action_request WHERE venture_id=%s", vid)
            + c("SELECT count(*) FROM proof_receipt")            # research writes no proof receipts
            + c("SELECT count(*) FROM policy_decision"))
        facts = {
            "smoke": "gate8-research",
            "repo_sha": os.environ.get("GITHUB_SHA", "local"),
            "venture_id": str(vid),
            "adapter": "tavily-search",
            "proposer": "anthropic-messages",
            "tavily_calls": counter["tavily"],
            "anthropic_calls": counter["anthropic"],
            "spend_ceiling_usd": _SPEND_CEILING_USD,
            "source_receipts": c("SELECT count(*) FROM source_receipt WHERE venture_id=%s", vid),
            "observations": c("SELECT count(*) FROM observation WHERE venture_id=%s", vid),
            "claims": c("SELECT count(*) FROM claim WHERE venture_id=%s", vid),
            "opportunities": c("SELECT count(*) FROM opportunity WHERE venture_id=%s", vid),
            "outcome": outcome,
            "governance_deltas": governance,
        }
        if run is not None:
            facts["run_id"] = str(run.research_run_id)
            facts["candidates"] = len(run.candidate_ids)
            facts["rejected_observations"] = len(run.rejected_observations)
        return facts
    finally:
        fresh.close()


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        _emit({"smoke": "gate8-research", "result": "CONFIG_ERROR", "reason": "DATABASE_URL not set"})
        return 2
    missing = [n for n in _REQUIRED if not os.environ.get(n)]
    if missing:
        _emit({"smoke": "gate8-research", "result": "CONFIG_ERROR", "missing": missing})
        return 2

    import psycopg

    from aidan_core import ventures
    from aidan_core.research import orchestration, sources

    counter = {"tavily": 0, "anthropic": 0}
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        vid = ventures.create_venture(conn, slug="gate8-live-smoke")
        ventures.append_mandate_version(conn, vid, content_hash=sources.content_hash(_MANDATE))
        adapter = BoundedTavily(counter, max_results=2)
        proposer = BoundedAnthropic(counter, max_questions=2)
        try:
            run = orchestration.run_research(
                conn, venture_id=vid, mandate_version=1, mandate_content=_MANDATE,
                run_key="gate8-live-smoke-1", adapter=adapter, proposer=proposer)
        except ConfigError:
            _emit({"smoke": "gate8-research", "result": "CONFIG_ERROR",
                   "tavily_calls": counter["tavily"], "anthropic_calls": counter["anthropic"]})
            return 2
        except InvalidAcquisitionError as exc:  # provider failure/refusal/malformed/bound -> fail closed
            facts = _facts(vid, counter, outcome=None)
            facts["result"] = "PROVIDER_FAILED_CLOSED"
            facts["reason"] = type(exc).__name__
            # sanitized, static classification of the exact failing branch (never a response body/secret)
            facts["provider_failure_code"] = getattr(exc, "provider_failure_code", "UNCLASSIFIED")
            http_status = getattr(exc, "provider_http_status", None)
            if http_status is not None:
                facts["provider_http_status"] = http_status        # safe integer only
            error_type = getattr(exc, "provider_error_type", None)
            if error_type is not None:
                facts["provider_error_type"] = error_type          # allowlisted static id only
            _emit(facts)
            return 3

        facts = _facts(vid, counter, outcome=run.outcome, run=run)
        # secret-leak check: values read in-process, never printed, only tested for absence in evidence.
        blob = json.dumps(facts)
        leaked = any(os.environ.get(n) and os.environ[n] in blob
                     for n in ("RESEARCH_TAVILY_API_KEY", "RESEARCH_ANTHROPIC_API_KEY"))
        facts["secret_leak_check"] = "FAIL" if leaked else "PASS"
        ok = (counter["tavily"] <= _MAX_TAVILY and counter["anthropic"] <= _MAX_ANTHROPIC
              and facts["governance_deltas"] == 0 and not leaked
              and run.outcome in _LEGIT_OUTCOMES)
        facts["result"] = "PASS" if ok else "ACCEPTANCE_FAILED"
        _emit(facts)
        sys.stderr.write(
            f"gate8 live smoke: {facts['result']} outcome={run.outcome} "
            f"tavily={counter['tavily']} anthropic={counter['anthropic']} "
            f"sources={facts['source_receipts']} obs={facts['observations']} "
            f"opps={facts['opportunities']} governance_deltas={facts['governance_deltas']}\n")
        return 0 if ok else 4
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
