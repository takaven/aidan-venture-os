"""Deterministic Source Receipt ingestion and freshness derivation.

Ingestion is the single canonical write boundary for acquired source material.
It creates the SOURCE ``evidence_record`` envelope and its ``source_receipt``
subtype atomically, keying acquisition idempotency on
``(venture_id, acquisition_key)``. The authoritative content hash lives on the
``evidence_record`` envelope; ``source_receipt`` holds SOURCE-specific
provenance only. Freshness is derived on demand from immutable temporal metadata
plus an explicit context — never persisted as an authoritative value.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from psycopg.types.json import Json

from .. import audit, db
from ..errors import IdempotencyConflictError
from .adapters import AcquiredSource


def content_hash(content: str) -> str:
    """Deterministic SHA-256 of the exact acquired content. Proves identity, not truth."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IngestResult:
    evidence_record_id: str
    created: bool  # True if a new receipt was created, False for an idempotent retry


def ingest(conn, venture_id: str, acquired: AcquiredSource, *, actor: str = "ingest") -> IngestResult:
    """Ingest a validated acquired source into the canonical evidence envelope.

    Idempotent per ``(venture_id, acquisition_key)``: an identical retry returns
    the existing receipt; the same key with different content is a hard conflict.
    """
    digest = content_hash(acquired.content)
    with db.transaction(conn) as cur:
        cur.execute(
            """
            SELECT sr.evidence_record_id, er.content_hash
            FROM source_receipt sr
            JOIN evidence_record er ON er.id = sr.evidence_record_id
            WHERE sr.venture_id = %s AND sr.acquisition_key = %s
            """,
            (venture_id, acquired.acquisition_key),
        )
        row = cur.fetchone()
        if row is not None:
            existing_id, existing_hash = row
            if existing_hash != digest:
                raise IdempotencyConflictError(
                    f"acquisition key {acquired.acquisition_key!r} reused with different content"
                )
            return IngestResult(existing_id, created=False)

        cur.execute(
            "INSERT INTO evidence_record (venture_id, kind, content_hash) "
            "VALUES (%s, 'SOURCE', %s) RETURNING id",
            (venture_id, digest),
        )
        evidence_record_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO source_receipt
                (evidence_record_id, venture_id, locator, source_type, retrieved_at, retrieved_by,
                 acquisition_key, published_at, publication_time_known, excerpt, snapshot_ref,
                 reliability_code, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                evidence_record_id, venture_id, acquired.locator, acquired.source_type,
                acquired.retrieved_at, acquired.retrieved_by, acquired.acquisition_key,
                acquired.published_at, acquired.publication_time_known, acquired.excerpt,
                acquired.snapshot_ref, acquired.reliability_code, Json(acquired.metadata),
            ),
        )
        audit.record_event(
            cur, event_type="evidence.source_ingested", actor=actor, venture_id=venture_id,
            payload={
                "evidence_record_id": str(evidence_record_id),
                "locator": acquired.locator,
                "retrieved_by": acquired.retrieved_by,
                "acquisition_key": acquired.acquisition_key,
            },
        )
    return IngestResult(evidence_record_id, created=True)


def get_source_receipt(conn, evidence_record_id: str):
    """Return the receipt joined to its envelope (content_hash from the envelope)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sr.evidence_record_id, sr.venture_id, sr.locator, sr.source_type,
                   sr.retrieved_at, sr.retrieved_by, sr.acquisition_key, sr.published_at,
                   sr.publication_time_known, sr.excerpt, sr.snapshot_ref, sr.reliability_code,
                   sr.metadata, er.content_hash, er.kind
            FROM source_receipt sr
            JOIN evidence_record er ON er.id = sr.evidence_record_id
            WHERE sr.evidence_record_id = %s
            """,
            (evidence_record_id,),
        )
        return cur.fetchone()


def evaluate_freshness(
    *,
    published_at: Optional[datetime],
    publication_time_known: bool,
    as_of: datetime,
    max_age: timedelta,
) -> str:
    """Derive freshness from immutable temporal metadata + explicit context.

    Returns CURRENT / STALE / UNCERTAIN_FRESHNESS. There is no universal
    threshold: ``as_of`` and ``max_age`` are supplied per decision context.
    """
    if not publication_time_known or published_at is None:
        return "UNCERTAIN_FRESHNESS"
    if as_of - published_at <= max_age:
        return "CURRENT"
    return "STALE"
