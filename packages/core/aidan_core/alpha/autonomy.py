"""Autonomous-run classification (Gate 8 Slice 3).

Two DISTINCT dimensions, never conflated:

  * assistance class — CLEAN_AUTONOMOUS_ALPHA vs HUMAN_ASSISTED — derived from canonical
    evidence. A predefined governance ``approval`` does NOT invalidate CLEAN; any unplanned
    human intervention (``alpha_intervention``: reasoning/code/deployment/provider/outcome
    correction) makes the venture's runs HUMAN_ASSISTED. The classifier reads durable state; a
    caller cannot self-certify.

  * evidence class — REAL vs SIMULATED. Whether a market action executed through a LIVE provider
    transport is NOT currently recorded in canonical state (Slice 2/3 use a fake transport), so
    no run can be classified REAL yet. This is an explicit, documented Slice-5 gap — the smallest
    future correction is to persist provider-transport provenance on the execution/source. Until
    then evidence class is conservatively SIMULATED and is never faked from a caller flag.

``is_clean_autonomous_alpha`` requires BOTH (CLEAN assistance AND REAL evidence), so a Slice-3
synthetic fixture can never be reported as a real clean-autonomous Alpha success.
"""
from __future__ import annotations

from typing import Optional

from .. import audit, db

CLEAN = "CLEAN_AUTONOMOUS_ALPHA"
HUMAN_ASSISTED = "HUMAN_ASSISTED"
REAL = "REAL"
SIMULATED = "SIMULATED"

# Unplanned interventions only — PREDEFINED_APPROVAL is proven by the immutable `approval`
# record and is intentionally NOT a member (never duplicated here).
INTERVENTION_KINDS = frozenset(
    {"REASONING_CORRECTION", "CODE_REPAIR", "DEPLOYMENT_REPAIR", "PROVIDER_REPAIR", "OUTCOME_TRANSCRIPTION"})


def record_intervention(
    conn,
    venture_id: str,
    *,
    intervention_kind: str,
    intervention_stage: str,
    occurred_at,
    reason: Optional[str] = None,
    related_action_request_id: Optional[str] = None,
    actor: str = "operator",
) -> str:
    """Append an immutable unplanned-intervention record. Rejects PREDEFINED_APPROVAL (that is a
    governance ``approval``, not an unplanned correction) and any kind outside the vocabulary."""
    if intervention_kind == "PREDEFINED_APPROVAL":
        raise ValueError("predefined approval is recorded via the canonical `approval`, not alpha_intervention")
    if intervention_kind not in INTERVENTION_KINDS:
        raise ValueError(f"unknown intervention_kind {intervention_kind!r}; allowed: {sorted(INTERVENTION_KINDS)}")
    if not intervention_stage or not str(intervention_stage).strip():
        raise ValueError("intervention_stage is required")
    with db.transaction(conn) as cur:
        cur.execute(
            "INSERT INTO alpha_intervention "
            "(venture_id, intervention_kind, intervention_stage, related_action_request_id, reason, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (venture_id, intervention_kind, intervention_stage, related_action_request_id, reason, occurred_at))
        iid = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="alpha.intervention_recorded", actor=actor, venture_id=venture_id,
            action_id=related_action_request_id,
            payload={"alpha_intervention_id": str(iid), "intervention_kind": intervention_kind,
                     "intervention_stage": intervention_stage})
    return str(iid)


def assistance_class(conn, venture_id: str) -> str:
    """CLEAN unless ≥1 unplanned intervention exists for THIS venture. Predefined approvals do
    not count. Derived from durable state; other ventures' interventions are ignored."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM alpha_intervention WHERE venture_id = %s", (venture_id,))
        return HUMAN_ASSISTED if cur.fetchone()[0] > 0 else CLEAN


def evidence_class(conn, venture_id: str) -> str:
    """REAL vs SIMULATED. Canonical state does not yet record live-provider execution provenance
    (Slice-5 gap), so this is conservatively SIMULATED and never derived from a caller flag."""
    return SIMULATED


def is_clean_autonomous_alpha(conn, venture_id: str) -> bool:
    """A real clean-autonomous Alpha requires BOTH CLEAN assistance AND REAL evidence. Always
    False for synthetic fixtures — no simulated run can be reported as a real Alpha success."""
    return assistance_class(conn, venture_id) == CLEAN and evidence_class(conn, venture_id) == REAL


def autonomy_summary(conn, venture_id: str) -> dict:
    return {
        "assistance_class": assistance_class(conn, venture_id),
        "evidence_class": evidence_class(conn, venture_id),
        "clean_autonomous_alpha": is_clean_autonomous_alpha(conn, venture_id),
    }
