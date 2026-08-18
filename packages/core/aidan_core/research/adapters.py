"""Provider-neutral research acquisition contract.

A ``ResearchAdapter`` acquires untrusted source DATA only. It never returns
canonical evidence ids, Observations, Claims, Interpretations, Opportunities,
policy decisions or ActionRequests, and it never writes PostgreSQL. Acquisition
and canonical ingestion are separate operations (see ``sources.py``).

Validation is stdlib-only: for Slice 1 the acquisition envelope is small enough
that explicit validation is as robust as a third-party model and adds no
dependency. Acquired content is DATA — any instructions inside it carry no
authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from ..errors import InvalidAcquisitionError

SOURCE_TYPES = frozenset(
    {"WEB_PAGE", "DOCUMENT", "DATASET", "API_RESPONSE", "OTHER"}
)
RELIABILITY_CODES = frozenset(
    {"PRIMARY", "SECONDARY", "AUTHORITATIVE", "ANECDOTAL", "DIRECT_MEASUREMENT", "COMMENTARY", "UNKNOWN"}
)


@dataclass(frozen=True)
class AcquiredSource:
    """Typed, validated result of one acquisition — untrusted source data.

    ``content`` is the exact selected/normalised source text used for hashing.
    Instances validate on construction; invalid untrusted input raises
    :class:`InvalidAcquisitionError` before any canonical persistence.
    """

    locator: str
    source_type: str
    content: str
    retrieved_at: datetime
    retrieved_by: str
    acquisition_key: str
    published_at: Optional[datetime] = None
    publication_time_known: bool = False
    excerpt: Optional[str] = None
    snapshot_ref: Optional[str] = None
    reliability_code: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        def require(cond: bool, msg: str) -> None:
            if not cond:
                raise InvalidAcquisitionError(msg)

        require(isinstance(self.locator, str) and self.locator.strip() != "", "locator is required")
        require(self.source_type in SOURCE_TYPES, f"source_type must be one of {sorted(SOURCE_TYPES)}")
        require(isinstance(self.content, str) and self.content != "", "content is required and non-empty")
        require(isinstance(self.retrieved_at, datetime), "retrieved_at must be a datetime")
        require(isinstance(self.retrieved_by, str) and self.retrieved_by.strip() != "", "retrieved_by is required")
        require(isinstance(self.acquisition_key, str) and self.acquisition_key.strip() != "", "acquisition_key is required")
        require(self.published_at is None or isinstance(self.published_at, datetime), "published_at must be a datetime or None")
        require(isinstance(self.publication_time_known, bool), "publication_time_known must be a bool")
        require(
            self.reliability_code is None or self.reliability_code in RELIABILITY_CODES,
            f"reliability_code must be None or one of {sorted(RELIABILITY_CODES)}",
        )
        require(isinstance(self.metadata, dict), "metadata must be an object")
        # If a publication time is asserted known, it must actually be present.
        require(
            not self.publication_time_known or self.published_at is not None,
            "publication_time_known implies published_at is set",
        )


@runtime_checkable
class ResearchAdapter(Protocol):
    """Read-only acquisition of untrusted source material. Returns DATA only."""

    adapter_id: str

    def acquire(self, query: str) -> list[AcquiredSource]:
        ...
