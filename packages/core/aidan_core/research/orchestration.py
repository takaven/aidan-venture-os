"""Mandate-driven autonomous research loop (Gate 2, Slice 4).

The only venture-specific starting input is a canonical Venture Mandate version,
whose content is deterministically verified before research begins. A provider-
neutral ResearchAdapter acquires untrusted DATA; a provider-neutral
ResearchProposer proposes typed artifacts (never touching the database). This
deterministic kernel verifies provenance and performs every canonical write.

Load-bearing control — no invented evidence: a proposed Observation is persisted
only if its excerpt is an exact substring of the exact acquired content whose
hash backs its Source Receipt. Fabricated excerpts are rejected and never become
evidence or SUPPORTS relations. This is not a workflow engine, scheduler, queue,
agent framework or Gate 4 runtime — it coordinates existing primitives once,
synchronously.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .. import audit, db
from ..errors import IdempotencyConflictError, MandateMismatchError, NotFoundError, OpportunityNotReadyError
from . import assumptions, claims, interpretations, killcase, observations, opportunities, sources
from .proposals import ObservationProposal, ResearchProposal


@dataclass(frozen=True)
class ResearchRunResult:
    research_run_id: str
    venture_id: str
    mandate_version: int
    outcome: str
    question_ids: list
    source_receipt_ids: list
    observation_ids: list
    rejected_observations: list
    claim_states: dict
    interpretation_ids: list
    assumption_ids: list
    opportunity_statuses: dict
    kill_case_ids: list
    candidate_ids: list
    critical_unknowns: list


def _verify_mandate(cur, venture_id: str, mandate_version: int, mandate_hash: str) -> None:
    cur.execute(
        "SELECT content_hash FROM venture_mandate_version WHERE venture_id = %s AND version = %s",
        (venture_id, mandate_version),
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"mandate version {mandate_version} not found for this venture")
    if row[0] != mandate_hash:
        raise MandateMismatchError("supplied mandate content does not match the canonical version hash")


def run_research(
    conn,
    *,
    venture_id: str,
    mandate_version: int,
    mandate_content: str,
    run_key: str,
    adapter,
    proposer,
    max_sources_per_question: int = 5,
    actor: str = "research",
) -> ResearchRunResult:
    """Execute one bounded, synchronous, idempotent research run for a Mandate."""
    mandate_hash = sources.content_hash(mandate_content)

    with db.transaction(conn) as cur:
        _verify_mandate(cur, venture_id, mandate_version, mandate_hash)
        cur.execute(
            "INSERT INTO research_run (venture_id, mandate_version, mandate_hash, run_key) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (venture_id, run_key) DO NOTHING RETURNING id",
            (venture_id, mandate_version, mandate_hash, run_key),
        )
        row = cur.fetchone()
        if row is not None:
            run_id = row[0]
            audit.record_event(
                cur, event_type="research.run_started", actor=actor, venture_id=venture_id,
                payload={"research_run_id": str(run_id), "mandate_version": mandate_version},
            )
        else:
            cur.execute(
                "SELECT id, mandate_hash FROM research_run WHERE venture_id = %s AND run_key = %s",
                (venture_id, run_key),
            )
            run_id, existing_hash = cur.fetchone()
            if existing_hash != mandate_hash:
                raise IdempotencyConflictError(
                    f"run key {run_key!r} reused with a different mandate"
                )

    # Pipeline is idempotent end-to-end, so an exact retry converges with no
    # duplicate canonical rows or audit events.
    outcome, ids = _execute_pipeline(
        conn, run_id, venture_id, mandate_content, adapter, proposer, max_sources_per_question
    )

    with db.transaction(conn) as cur:
        cur.execute("SELECT outcome FROM research_run WHERE id = %s FOR UPDATE", (run_id,))
        prev = cur.fetchone()[0]
        if prev is None:
            cur.execute(
                "UPDATE research_run SET outcome = %s, ended_at = now() WHERE id = %s", (outcome, run_id)
            )
            audit.record_event(
                cur, event_type="research.run_completed", actor=actor, venture_id=venture_id,
                payload={"research_run_id": str(run_id), "outcome": outcome},
            )
            final_outcome = outcome
        else:
            final_outcome = prev

    return ResearchRunResult(
        research_run_id=run_id, venture_id=venture_id, mandate_version=mandate_version,
        outcome=final_outcome, **ids,
    )


def _execute_pipeline(conn, run_id, venture_id, mandate_content, adapter, proposer, max_sources):
    # --- research questions (reasoning artifact) ---
    questions = list(proposer.research_questions(mandate_content) or [])
    question_ids = []
    with db.transaction(conn) as cur:
        for i, q in enumerate(questions):
            if not isinstance(q, str) or not q.strip():
                raise ValueError("research question must be a non-empty string")
            cur.execute(
                "INSERT INTO research_question (research_run_id, venture_id, ordinal, question) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (research_run_id, ordinal) DO NOTHING RETURNING id",
                (run_id, venture_id, i, q),
            )
            r = cur.fetchone()
            if r is None:
                cur.execute("SELECT id FROM research_question WHERE research_run_id = %s AND ordinal = %s", (run_id, i))
                r = cur.fetchone()
            question_ids.append(r[0])

    # --- acquisition (untrusted DATA only; bounded; failures never fabricate) ---
    acquired = []  # (receipt_id, normalized_content, locator)
    for q in questions:
        try:
            results = list(adapter.acquire(q))
        except Exception:  # provider failure must not create invented evidence
            continue
        for src in results[:max_sources]:
            res = sources.ingest(conn, venture_id, src, actor="research")
            acquired.append((res.evidence_record_id, sources.normalize(src.content), src.locator))

    # --- proposals over acquired data (proposer never gets a connection/ids) ---
    src_data = [{"index": i, "content": c, "locator": loc} for i, (_r, c, loc) in enumerate(acquired)]
    proposal = proposer.propose(mandate_content, src_data) if acquired else ResearchProposal()

    # --- verify + persist Observations (exact-substring anchoring) ---
    obs_key_to_id: dict = {}
    rejected: list = []
    for op in proposal.observations:
        if not isinstance(op, ObservationProposal):
            raise ValueError("invalid observation proposal")
        if op.source_index < 0 or op.source_index >= len(acquired):
            rejected.append({"key": op.key, "reason": "unknown_source"})
            continue
        receipt_id, content, _loc = acquired[op.source_index]
        if not op.excerpt or op.excerpt not in content:
            rejected.append({"key": op.key, "reason": "excerpt_not_in_source"})
            continue
        r = observations.create_observation(
            conn, venture_id, source_evidence_id=receipt_id, statement=op.statement,
            observation_key=op.key, source_locator=op.locator, excerpt=op.excerpt,
        )
        obs_key_to_id[op.key] = r.evidence_record_id

    # --- Claims + stances (only over persisted observations) ---
    claim_key_to_id: dict = {}
    for cp in proposal.claims:
        cid = claims.create_claim(conn, venture_id, statement=cp.statement, claim_key=cp.key).evidence_record_id
        claim_key_to_id[cp.key] = cid
        for ok in cp.supports:
            if ok in obs_key_to_id:
                claims.link_evidence(conn, claim_id=cid, observation_id=obs_key_to_id[ok], stance="SUPPORTS")
        for ok in cp.contradicts:
            if ok in obs_key_to_id:
                claims.link_evidence(conn, claim_id=cid, observation_id=obs_key_to_id[ok], stance="CONTRADICTS")

    # --- Interpretations ---
    interp_key_to_id: dict = {}
    for ip in proposal.interpretations:
        iid = interpretations.create_interpretation(
            conn, venture_id, statement=ip.statement, interpretation_key=ip.key, produced_by=ip.produced_by
        ).interpretation_id
        interp_key_to_id[ip.key] = iid
        for ck in ip.claim_keys:
            if ck in claim_key_to_id:
                interpretations.link_claim(conn, interpretation_id=iid, claim_id=claim_key_to_id[ck])

    # --- Assumptions ---
    assume_key_to_id: dict = {}
    for ap in proposal.assumptions:
        aid = assumptions.create_assumption(
            conn, venture_id, proposition=ap.proposition, assumption_key=ap.key, importance=ap.importance,
            confidence=ap.confidence, consequence_if_false=ap.consequence_if_false, cheapest_test=ap.cheapest_test,
        ).assumption_id
        assume_key_to_id[ap.key] = aid
        for ck in ap.claim_keys:
            if ck in claim_key_to_id:
                assumptions.link_claim(conn, assumption_id=aid, claim_id=claim_key_to_id[ck])
        for ik in ap.interpretation_keys:
            if ik in interp_key_to_id:
                assumptions.link_interpretation(conn, assumption_id=aid, interpretation_id=interp_key_to_id[ik])

    # --- Opportunities + Kill Cases + guarded finalization ---
    opportunity_statuses: dict = {}
    kill_case_ids: list = []
    candidate_ids: list = []
    critical_unknowns: list = []
    for opp in proposal.opportunities:
        oid = opportunities.create_opportunity(
            conn, venture_id, opportunity_key=opp.key, buyer_hypothesis=opp.buyer_hypothesis,
            problem_hypothesis=opp.problem_hypothesis, acquisition_hypothesis=opp.acquisition_hypothesis,
            critical_unknown=opp.critical_unknown,
        ).opportunity_id
        if opp.critical_unknown:
            critical_unknowns.append(opp.critical_unknown)
        linked_claim_ids = []
        for ck in opp.claim_keys:
            if ck in claim_key_to_id:
                opportunities.link_claim(conn, opportunity_id=oid, claim_id=claim_key_to_id[ck])
                linked_claim_ids.append(claim_key_to_id[ck])
        for ak in opp.assumption_keys:
            if ak in assume_key_to_id:
                opportunities.link_assumption(conn, opportunity_id=oid, assumption_id=assume_key_to_id[ak])
        for ik in opp.interpretation_keys:
            if ik in interp_key_to_id:
                opportunities.link_interpretation(conn, opportunity_id=oid, interpretation_id=interp_key_to_id[ik])
        if opp.kill_case is not None:
            kc = killcase.create_kill_case(
                conn, opportunity_id=oid, kill_case_key=f"{opp.key}-kc", disposition=opp.kill_case.disposition
            ).kill_case_id
            kill_case_ids.append(kc)
            for dp in opp.kill_case.dimensions:
                dim_id = killcase.add_dimension(
                    conn, kill_case_id=kc, dimension=dp.dimension, assessment=dp.assessment, rationale=dp.rationale
                ).dimension_id
                for ck in dp.claim_keys:
                    if ck in claim_key_to_id:
                        killcase.link_dimension_claim(conn, dimension_id=dim_id, claim_id=claim_key_to_id[ck])

        # Autonomous readiness: at least one genuine positive supporting path.
        has_support = any(
            claims.claim_state(conn, cid) in ("SUPPORTED", "DISPUTED") for cid in linked_claim_ids
        )
        if has_support:
            try:
                opportunities.finalize_candidate(conn, oid)
                candidate_ids.append(oid)
            except OpportunityNotReadyError:
                pass  # structurally incomplete -> stays DRAFT
        opportunity_statuses[str(oid)] = opportunities.get_status(conn, oid)

    verified_observations = len(obs_key_to_id)
    if candidate_ids:
        outcome = "OPPORTUNITIES_FOUND"
    elif verified_observations == 0:
        outcome = "INSUFFICIENT_EVIDENCE"
    else:
        outcome = "NO_CREDIBLE_OPPORTUNITY"

    ids = {
        "question_ids": question_ids,
        "source_receipt_ids": [rid for rid, _c, _l in acquired],
        "observation_ids": list(obs_key_to_id.values()),
        "rejected_observations": rejected,
        "claim_states": {str(cid): claims.claim_state(conn, cid) for cid in claim_key_to_id.values()},
        "interpretation_ids": list(interp_key_to_id.values()),
        "assumption_ids": list(assume_key_to_id.values()),
        "opportunity_statuses": opportunity_statuses,
        "kill_case_ids": kill_case_ids,
        "candidate_ids": candidate_ids,
        "critical_unknowns": critical_unknowns,
    }
    return outcome, ids
