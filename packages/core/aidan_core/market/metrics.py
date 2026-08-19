"""Derived market metrics (Gate 7 Slice 3).

Deterministic counts computed BY QUERY over canonical ``market_observation`` rows — there is
no metrics table and no scalar market/traction/demand score. Only observation types actually
supported by Slice 2 are exposed. Duplicates never inflate a count (Slice-2 source-scoped
dedupe already collapses a repeated external event to one row).

Rate discipline: a rate is returned ONLY when its denominator exists canonically. No canonical
eligible-send / recipient-population denominator is frozen anywhere in the current schema, so
``reply_rate`` (and any other rate) is UNAVAILABLE — it is never fabricated by dividing by an
arbitrary action/attempt count.
"""
from __future__ import annotations

from typing import Optional

# Mirrors the Slice-2 observation vocabulary; a metric exists only for a supported type.
_COUNTABLE = ("DELIVERED", "BOUNCED", "OPENED", "CLICKED", "REPLIED", "UNSUBSCRIBE")


def market_metrics(conn, market_action_spec_id: str) -> dict[str, int]:
    """Deterministic per-type counts derived from canonical observations for one market action."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT observation_type, count(*) FROM market_observation "
            "WHERE market_action_spec_id = %s GROUP BY observation_type", (market_action_spec_id,))
        counts = {t: int(n) for t, n in cur.fetchall()}
    return {f"{t.lower()}_count": counts.get(t, 0) for t in _COUNTABLE}


def reply_rate(conn, market_action_spec_id: str) -> Optional[float]:
    """UNAVAILABLE by design: no canonical eligible-send denominator exists.

    Returns ``None`` rather than fabricating a rate from an arbitrary denominator. A real rate
    can only be computed once a deterministic recipient-population / eligible-send count is
    frozen canonically (not in this slice).
    """
    return None
