"""Freshness derivation + acquisition-input validation (mostly pure)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aidan_core import ventures
from aidan_core.errors import InvalidAcquisitionError
from aidan_core.research import sources
from aidan_core.research.adapters import AcquiredSource

UTC = timezone.utc
AS_OF = datetime(2026, 6, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# Freshness is derived from immutable metadata + explicit context (pure).
# --------------------------------------------------------------------------
def test_freshness_current():
    assert sources.evaluate_freshness(
        published_at=datetime(2026, 5, 20, tzinfo=UTC), publication_time_known=True,
        as_of=AS_OF, max_age=timedelta(days=30),
    ) == "CURRENT"


def test_freshness_stale():
    assert sources.evaluate_freshness(
        published_at=datetime(2026, 1, 1, tzinfo=UTC), publication_time_known=True,
        as_of=AS_OF, max_age=timedelta(days=30),
    ) == "STALE"


def test_freshness_unknown_publication_time():
    assert sources.evaluate_freshness(
        published_at=None, publication_time_known=False, as_of=AS_OF, max_age=timedelta(days=30),
    ) == "UNCERTAIN_FRESHNESS"


def test_freshness_depends_on_context_window():
    published = datetime(2026, 5, 20, tzinfo=UTC)  # 12 days before AS_OF
    assert sources.evaluate_freshness(
        published_at=published, publication_time_known=True, as_of=AS_OF, max_age=timedelta(days=30)
    ) == "CURRENT"
    assert sources.evaluate_freshness(
        published_at=published, publication_time_known=True, as_of=AS_OF, max_age=timedelta(days=5)
    ) == "STALE"


def test_receipt_has_no_persisted_freshness_column(migrated):
    # Freshness must never be frozen onto the immutable receipt.
    with migrated.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'source_receipt'"
        )
        cols = {r[0] for r in cur.fetchall()}
    assert "freshness_category" not in cols and "freshness" not in cols


def test_freshness_evaluation_does_not_mutate_receipt(migrated):
    vid = ventures.create_venture(migrated, slug="fr-nomutate")
    res = sources.ingest(migrated, vid, AcquiredSource(
        locator="L", source_type="WEB_PAGE", content="c", retrieved_at=AS_OF,
        retrieved_by="a", acquisition_key="k",
        published_at=datetime(2026, 1, 1, tzinfo=UTC), publication_time_known=True,
    ))
    before = sources.get_source_receipt(migrated, res.evidence_record_id)
    sources.evaluate_freshness(
        published_at=before[7], publication_time_known=before[8], as_of=AS_OF, max_age=timedelta(days=1)
    )
    after = sources.get_source_receipt(migrated, res.evidence_record_id)
    assert before == after  # pure evaluation touched nothing


# --------------------------------------------------------------------------
# Untrusted acquisition input validation (pure).
# --------------------------------------------------------------------------
def _base(**kw):
    b = dict(locator="L", source_type="WEB_PAGE", content="c",
             retrieved_at=AS_OF, retrieved_by="a", acquisition_key="k")
    b.update(kw)
    return b


def test_invalid_acquisitions_rejected():
    with pytest.raises(InvalidAcquisitionError):
        AcquiredSource(**_base(locator=""))          # missing locator
    with pytest.raises(InvalidAcquisitionError):
        AcquiredSource(**_base(content=""))          # empty content
    with pytest.raises(InvalidAcquisitionError):
        AcquiredSource(**_base(source_type="BOGUS")) # invalid source type
    with pytest.raises(InvalidAcquisitionError):
        AcquiredSource(**_base(retrieved_by=""))     # missing adapter identity
    with pytest.raises(InvalidAcquisitionError):
        AcquiredSource(**_base(publication_time_known=True))  # known but no published_at
    with pytest.raises(InvalidAcquisitionError):
        AcquiredSource(**_base(reliability_code="TRUSTWORTHY"))  # not an allowed code
