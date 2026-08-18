"""Provider-neutral reasoning contracts. Proposers PROPOSE; the kernel PERSISTS.

A ``ResearchProposer`` receives the Mandate text and acquired source material as
untrusted DATA (by index + content only — never a DB connection, never canonical
ids) and returns typed proposals. It cannot write PostgreSQL, set canonical
state, set Claim state, finalize opportunities, or affect governance/capital.
Deterministic orchestration verifies provenance and performs all canonical
writes. Concrete providers live in adapter/proposer implementations, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class ObservationProposal:
    source_index: int          # index into the acquired sources passed to the proposer
    excerpt: str               # must be an exact substring of that source's content
    statement: str
    key: str
    locator: Optional[str] = None


@dataclass(frozen=True)
class ClaimProposal:
    key: str
    statement: str
    supports: tuple = ()        # observation proposal keys asserting SUPPORTS
    contradicts: tuple = ()      # observation proposal keys asserting CONTRADICTS


@dataclass(frozen=True)
class InterpretationProposal:
    key: str
    statement: str
    produced_by: str
    claim_keys: tuple = ()


@dataclass(frozen=True)
class AssumptionProposal:
    key: str
    proposition: str
    importance: str
    confidence: str
    consequence_if_false: str
    cheapest_test: str
    claim_keys: tuple = ()
    interpretation_keys: tuple = ()


@dataclass(frozen=True)
class DimensionProposal:
    dimension: str
    assessment: str
    rationale: str
    claim_keys: tuple = ()


@dataclass(frozen=True)
class KillCaseProposal:
    disposition: str
    dimensions: tuple = ()       # DimensionProposal[]


@dataclass(frozen=True)
class OpportunityProposal:
    key: str
    buyer_hypothesis: str
    problem_hypothesis: str
    critical_unknown: str
    claim_keys: tuple = ()
    assumption_keys: tuple = ()
    interpretation_keys: tuple = ()
    acquisition_hypothesis: Optional[str] = None
    kill_case: Optional[KillCaseProposal] = None


@dataclass(frozen=True)
class ResearchProposal:
    observations: tuple = ()
    claims: tuple = ()
    interpretations: tuple = ()
    assumptions: tuple = ()
    opportunities: tuple = ()


class ResearchProposer(Protocol):
    """Reasoning boundary. Proposes only; never writes canonical state."""

    def research_questions(self, mandate: str) -> list:
        """Derive research questions from the Mandate text (reasoning artifact)."""
        ...

    def propose(self, mandate: str, sources: list) -> ResearchProposal:
        """Given the Mandate and acquired sources (as data: [{index, content, locator}]),
        return typed proposals."""
        ...
