"""Gate 8 — provider-neutral proposal pre-flight validation (deterministic; no DB, no network).

Proves ``orchestration.validate_proposal`` rejects a malformed ResearchProposal (bad categorical enum
or missing required field) against the authoritative kernel constants BEFORE any canonical write —
independent of any provider's JSON schema. Reproduces the live defect class
(AssumptionProposal.importance outside {CRITICAL,HIGH,LOW,MEDIUM}) and its siblings. Codes are static
and content-free (never the offending model value).
"""
from __future__ import annotations

import pytest

from aidan_core.errors import InvalidProposalError
from aidan_core.research.orchestration import validate_proposal
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


def _valid_proposal():
    return ResearchProposal(
        observations=(ObservationProposal(source_index=0, excerpt="ex", statement="s", key="o1"),),
        claims=(ClaimProposal(key="c1", statement="a claim", supports=("o1",)),),
        interpretations=(InterpretationProposal(key="i1", statement="an interp", produced_by="x"),),
        assumptions=(AssumptionProposal(key="a1", proposition="p", importance="HIGH", confidence="LOW",
                                        consequence_if_false="c", cheapest_test="t"),),
        opportunities=(OpportunityProposal(
            key="opp1", buyer_hypothesis="B", problem_hypothesis="P", critical_unknown="U",
            kill_case=KillCaseProposal(disposition="PROCEED_WITH_RISKS", dimensions=(
                DimensionProposal(dimension="WTP_WEAKNESS", assessment="MATERIAL_RISK", rationale="r"),))),),
    )


def test_valid_proposal_passes():
    validate_proposal(_valid_proposal())   # must not raise


def _assumption(**over):
    base = dict(key="a1", proposition="p", importance="HIGH", confidence="LOW",
                consequence_if_false="c", cheapest_test="t")
    base.update(over)
    return ResearchProposal(assumptions=(AssumptionProposal(**base),))


def _killcase(**over):
    base = dict(disposition="PROCEED_WITH_RISKS",
                dimensions=(DimensionProposal(dimension="WTP_WEAKNESS", assessment="MATERIAL_RISK", rationale="r"),))
    base.update(over)
    return ResearchProposal(opportunities=(OpportunityProposal(
        key="o", buyer_hypothesis="B", problem_hypothesis="P", critical_unknown="U",
        kill_case=KillCaseProposal(**base)),))


@pytest.mark.parametrize("proposal,code", [
    (_assumption(importance="MODERATE"), "ASSUMPTION_IMPORTANCE"),           # the live defect class
    (_assumption(importance="high"), "ASSUMPTION_IMPORTANCE"),               # case-sensitive
    (_assumption(confidence="VERY_HIGH"), "ASSUMPTION_CONFIDENCE"),
    (_assumption(proposition=""), "ASSUMPTION_PROPOSITION"),
    (_assumption(cheapest_test=""), "ASSUMPTION_CHEAPEST_TEST"),
    (ResearchProposal(observations=(ObservationProposal(source_index="0", excerpt="e", statement="s", key="o"),)),
     "OBSERVATION_SOURCE_INDEX"),
    (ResearchProposal(observations=(ObservationProposal(source_index=0, excerpt="e", statement="", key="o"),)),
     "OBSERVATION_STATEMENT"),
    (ResearchProposal(claims=(ClaimProposal(key="", statement="s"),)), "CLAIM_KEY"),
    (ResearchProposal(interpretations=(InterpretationProposal(key="i", statement="s", produced_by=""),)),
     "INTERPRETATION_PRODUCED_BY"),
    (ResearchProposal(opportunities=(OpportunityProposal(key="", buyer_hypothesis="B", problem_hypothesis="P",
                                                         critical_unknown="U"),)), "OPPORTUNITY_KEY"),
    (_killcase(disposition="MAYBE"), "KILLCASE_DISPOSITION"),
    (_killcase(dimensions=(DimensionProposal(dimension="NOT_A_DIM", assessment="MATERIAL_RISK", rationale="r"),)),
     "KILLCASE_DIMENSION"),
    (_killcase(dimensions=(DimensionProposal(dimension="WTP_WEAKNESS", assessment="CATASTROPHE", rationale="r"),)),
     "KILLCASE_ASSESSMENT"),
    (_killcase(dimensions=(DimensionProposal(dimension="WTP_WEAKNESS", assessment="MATERIAL_RISK", rationale=""),)),
     "KILLCASE_RATIONALE"),
])
def test_invalid_proposal_rejected_with_static_code(proposal, code):
    with pytest.raises(InvalidProposalError) as ei:
        validate_proposal(proposal)
    assert ei.value.proposal_code == code
    assert ei.value.provider_failure_code == "PROPOSAL_INVALID"


def test_proposal_code_is_static_and_content_free():
    with pytest.raises(InvalidProposalError) as ei:
        validate_proposal(_assumption(importance="TOTALLY-MADE-UP-VALUE-xyz"))
    assert "TOTALLY-MADE-UP-VALUE" not in ei.value.proposal_code   # the model value never appears
    assert ei.value.proposal_code == "ASSUMPTION_IMPORTANCE"
    assert str(ei.value) == ei.value.proposal_code                 # message is the static code only
