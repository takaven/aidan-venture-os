"""Deterministic replay fixtures for Gate 2 orchestration evals.

No network, no live provider, no secrets. Adapters replay canned source text;
proposers build proposals from the exact acquired content so excerpts are real
substrings. The SAME orchestration code processes every case — fixtures never
drive production branching.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aidan_core import ventures
from aidan_core.research import sources
from aidan_core.research.adapters import AcquiredSource
from aidan_core.research.killcase import REQUIRED_DIMENSIONS
from aidan_core.research.proposals import (
    AssumptionProposal,
    ClaimProposal,
    DimensionProposal,
    InterpretationProposal,
    KillCaseProposal,
    ObservationProposal,
    OpportunityProposal,
    ResearchProposal,
)

UTC = timezone.utc


# --------------------------------------------------------------------------
# Replay adapters / scripted proposer.
# --------------------------------------------------------------------------
class ReplayAdapter:
    adapter_id = "replay"

    def __init__(self, per_question: dict):
        self._pq = per_question

    def acquire(self, query: str) -> list:
        return list(self._pq.get(query, []))


class FailingAdapter:
    adapter_id = "failing"

    def acquire(self, query: str) -> list:
        raise RuntimeError("provider unavailable")


class ScriptedProposer:
    def __init__(self, questions: list, builder):
        self._questions = questions
        self._builder = builder

    def research_questions(self, mandate: str) -> list:
        return list(self._questions)

    def propose(self, mandate: str, sources_data: list) -> ResearchProposal:
        return self._builder(mandate, sources_data)


def acquired(content: str, *, key: str, locator: str = "https://example.com/x", published=None) -> AcquiredSource:
    return AcquiredSource(
        locator=locator, source_type="WEB_PAGE", content=content,
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), retrieved_by="replay", acquisition_key=key,
        published_at=published, publication_time_known=published is not None,
    )


def full_kill_case(disposition: str = "PROCEED_WITH_RISKS", assessment: str = "MATERIAL_RISK") -> KillCaseProposal:
    return KillCaseProposal(
        disposition=disposition,
        dimensions=tuple(DimensionProposal(dimension=d, assessment=assessment, rationale="r") for d in REQUIRED_DIMENSIONS),
    )


# --------------------------------------------------------------------------
# Mandate setup.
# --------------------------------------------------------------------------
def make_mandate(conn, slug: str, mandate_text: str = "MANDATE: build value for SMB finance teams."):
    vid = ventures.create_venture(conn, slug=slug)
    ventures.append_mandate_version(conn, vid, content_hash=sources.content_hash(mandate_text))
    return vid, 1, mandate_text


# --------------------------------------------------------------------------
# Proposal builders (each references exact acquired content).
# --------------------------------------------------------------------------
def build_credible(mandate, sources_data):
    content = sources_data[0]["content"]
    return ResearchProposal(
        observations=(ObservationProposal(source_index=0, excerpt=content[:24], statement="measured burden", key="o1"),),
        claims=(ClaimProposal(key="c1", statement="SMB reconciliation is burdensome", supports=("o1",)),),
        interpretations=(InterpretationProposal(key="i1", statement="this may drive WTP", produced_by="model.replay"),),
        assumptions=(AssumptionProposal(key="a1", proposition="teams will pay for automation", importance="HIGH",
                                        confidence="LOW", consequence_if_false="no market", cheapest_test="interview 5 finance managers"),),
        opportunities=(OpportunityProposal(key="opp1", buyer_hypothesis="SMB finance teams",
                                           problem_hypothesis="manual reconciliation", critical_unknown="willingness to pay",
                                           claim_keys=("c1",), assumption_keys=("a1",), interpretation_keys=("i1",),
                                           kill_case=full_kill_case()),),
    )


def build_contradicted(mandate, sources_data):
    # Two sources: one supports, one contradicts the same claim.
    return ResearchProposal(
        observations=(
            ObservationProposal(source_index=0, excerpt=sources_data[0]["content"][:20], statement="supports", key="o1"),
            ObservationProposal(source_index=1, excerpt=sources_data[1]["content"][:20], statement="contradicts", key="o2"),
        ),
        claims=(ClaimProposal(key="c1", statement="buyers have strong WTP", supports=("o1",), contradicts=("o2",)),),
        assumptions=(AssumptionProposal(key="a1", proposition="p", importance="HIGH", confidence="LOW",
                                        consequence_if_false="c", cheapest_test="t"),),
        opportunities=(OpportunityProposal(key="opp1", buyer_hypothesis="B", problem_hypothesis="P",
                                           critical_unknown="U", claim_keys=("c1",), assumption_keys=("a1",),
                                           kill_case=full_kill_case()),),
    )


def build_fabricated(mandate, sources_data):
    # One genuine excerpt (o1) and one fabricated excerpt (o2, absent from source).
    content = sources_data[0]["content"]
    return ResearchProposal(
        observations=(
            ObservationProposal(source_index=0, excerpt=content[:24], statement="genuine", key="o1"),
            ObservationProposal(source_index=0, excerpt="FABRICATED TEXT NOT IN SOURCE", statement="fabricated", key="o2"),
        ),
        # c1 supported by genuine o1; c2 supported ONLY by fabricated o2 (which is rejected).
        claims=(
            ClaimProposal(key="c1", statement="genuine claim", supports=("o1",)),
            ClaimProposal(key="c2", statement="fabricated-only claim", supports=("o2",)),
        ),
        assumptions=(AssumptionProposal(key="a1", proposition="p", importance="HIGH", confidence="LOW",
                                        consequence_if_false="c", cheapest_test="t"),),
        opportunities=(OpportunityProposal(key="opp1", buyer_hypothesis="B", problem_hypothesis="P",
                                           critical_unknown="U", claim_keys=("c1",), assumption_keys=("a1",),
                                           kill_case=full_kill_case()),),
    )


def build_unsupported_no_opportunity(mandate, sources_data):
    # Observations + a claim, but no opportunity proposed -> NO_CREDIBLE_OPPORTUNITY.
    return ResearchProposal(
        observations=(ObservationProposal(source_index=0, excerpt=sources_data[0]["content"][:20], statement="fact", key="o1"),),
        claims=(ClaimProposal(key="c1", statement="a fact-backed claim", supports=("o1",)),),
    )


def build_empty(mandate, sources_data):
    return ResearchProposal()


def build_injection_observation(mandate, sources_data):
    # Observe that the source literally contains hostile text (as data).
    content = sources_data[0]["content"]
    return ResearchProposal(
        observations=(ObservationProposal(source_index=0, excerpt=content[:40], statement="source contains an override string", key="o1"),),
        claims=(ClaimProposal(key="c1", statement="the page contains injection text", supports=("o1",)),),
    )
