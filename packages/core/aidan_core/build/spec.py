"""Immutable, venture-specific build specifications (Gate 5 Slice 1).

A ``build_spec`` freezes the venture-specific product intent and build acceptance
contract that an already-governed Gate 3 BUILD decision permits specialist
workers to implement. It is bound 1:1 to the BUILD ActionRequest and is
immutable: correcting product intent means a new governed BUILD decision /
ActionRequest / build_spec, never a mutation.

The product-intent fields (buyer, problem, value_proposition, primary_workflow,
differentiators, ...) are frozen REQUIREMENTS — build decisions, not market
evidence. Commercial/market truth stays in the canonical Gate 2/3 records this
spec references (opportunity, recommendation, investment decision); it is never
duplicated or re-derived here, and worker-authored prose can never become
canonical commercial truth.

Idempotency mirrors the Gate 4 execution_spec rule: the same BUILD ActionRequest
with identical normalized content converges on the existing row; ANY material
change of product intent or authorizing provenance is a hard
``IdempotencyConflictError`` — never a silent mutation (the row is immutable) and
never a silent return of a stale row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import (
    IdempotencyConflictError,
    InconsistentCanonicalStateError,
    NotFoundError,
)


@dataclass(frozen=True)
class BuildSpecResult:
    build_spec_id: str
    spec_hash: str
    created: bool


def compute_build_spec_hash(
    *,
    venture_id,
    action_request_id,
    source_investment_decision_id,
    source_recommendation_id,
    opportunity_id,
    buyer: str,
    problem: str,
    value_proposition: str,
    product_category: str,
    primary_workflow: str,
    differentiators: list,
    required_capabilities: list,
    excluded_capabilities: list,
    experience_principles: list,
    expected_output_contract: dict,
) -> str:
    """Deterministic identity over ALL material executable build authority.

    Covers the venture-specific product intent AND the concrete BUILD provenance
    identities, so any change of intent OR of the authorizing decision yields a
    different hash (and, under the same ActionRequest, a hard idempotency
    conflict). Capability sets are sorted (membership, not order, is semantic);
    narrative lists keep their order. DB-generated row ids/timestamps are
    excluded. Substrate identity is intentionally absent — deferred to Slice 2
    (no substrate exists to reference yet), so it cannot appear as fake
    provenance in the hash.
    """
    return canonical_payload_hash({
        "venture_id": str(venture_id),
        "action_request_id": str(action_request_id),
        "source_investment_decision_id": str(source_investment_decision_id),
        "source_recommendation_id": str(source_recommendation_id),
        "opportunity_id": str(opportunity_id),
        "buyer": buyer,
        "problem": problem,
        "value_proposition": value_proposition,
        "product_category": product_category,
        "primary_workflow": primary_workflow,
        "differentiators": list(differentiators),
        "required_capabilities": sorted(required_capabilities),
        "excluded_capabilities": sorted(excluded_capabilities),
        "experience_principles": list(experience_principles),
        "expected_output_contract": expected_output_contract,
    })


def _require_build_authority(
    cur, action_request_id, source_investment_decision_id, source_recommendation_id, opportunity_id
):
    """Verify the referenced Gate 3 records actually constitute the BUILD authority
    for this exact action, and return the action's venture_id.

    This is the kernel guard that a build_spec may only be frozen against a
    genuine, venture-consistent BUILD decision — not merely because an
    ActionRequest exists or its free-text ``action_type`` says 'build'.
    """
    cur.execute("SELECT venture_id FROM action_request WHERE id = %s", (action_request_id,))
    arow = cur.fetchone()
    if arow is None:
        raise NotFoundError(f"action_request {action_request_id} does not exist")
    venture_id = arow[0]

    cur.execute(
        "SELECT venture_id, decision, resulting_action_id, source_recommendation_id "
        "FROM investment_decision_record WHERE id = %s",
        (source_investment_decision_id,),
    )
    drow = cur.fetchone()
    if drow is None:
        raise NotFoundError(
            f"investment_decision_record {source_investment_decision_id} does not exist"
        )
    d_venture, d_decision, d_action, d_rec = drow
    if str(d_venture) != str(venture_id):
        raise InconsistentCanonicalStateError(
            "investment decision belongs to a different venture than the action"
        )
    if d_decision != "BUILD":
        raise InconsistentCanonicalStateError(
            f"referenced investment decision is {d_decision}, not BUILD; "
            "a build_spec requires a canonical BUILD decision"
        )
    if str(d_action) != str(action_request_id):
        raise InconsistentCanonicalStateError(
            "referenced BUILD decision did not result in this ActionRequest"
        )
    if str(d_rec) != str(source_recommendation_id):
        raise InconsistentCanonicalStateError(
            "source_recommendation_id does not match the BUILD decision's recommendation basis"
        )

    cur.execute(
        "SELECT opportunity_id FROM next_action_recommendation WHERE id = %s",
        (source_recommendation_id,),
    )
    rrow = cur.fetchone()
    if rrow is None:
        raise NotFoundError(
            f"next_action_recommendation {source_recommendation_id} does not exist"
        )
    if str(rrow[0]) != str(opportunity_id):
        raise InconsistentCanonicalStateError(
            "opportunity_id does not match the recommendation's opportunity"
        )
    return venture_id


def create_build_spec(
    conn,
    action_request_id: str,
    *,
    source_investment_decision_id: str,
    source_recommendation_id: str,
    opportunity_id: str,
    buyer: str,
    problem: str,
    value_proposition: str,
    product_category: str,
    primary_workflow: str,
    differentiators: Optional[list] = None,
    required_capabilities: Optional[list] = None,
    excluded_capabilities: Optional[list] = None,
    experience_principles: Optional[list] = None,
    expected_output_contract: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> BuildSpecResult:
    """Freeze the immutable build_spec for a governed BUILD ActionRequest.

    Idempotent per action: an identical re-freeze converges on the existing row;
    ANY materially changed field (product intent or authorizing provenance) is a
    deterministic :class:`IdempotencyConflictError`. The referenced Gate 3 records
    must genuinely constitute the BUILD authority for this exact action.
    """
    for name, value in (
        ("buyer", buyer), ("problem", problem), ("value_proposition", value_proposition),
        ("product_category", product_category), ("primary_workflow", primary_workflow),
    ):
        if not value or not str(value).strip():
            raise ValueError(f"{name} is required for a venture-specific build_spec")

    differentiators = list(differentiators or [])
    required_capabilities = list(required_capabilities or [])
    excluded_capabilities = list(excluded_capabilities or [])
    experience_principles = list(experience_principles or [])
    expected_output_contract = expected_output_contract or {}

    with db.transaction(conn) as cur:
        venture_id = _require_build_authority(
            cur, action_request_id, source_investment_decision_id,
            source_recommendation_id, opportunity_id,
        )

        spec_hash = compute_build_spec_hash(
            venture_id=venture_id, action_request_id=action_request_id,
            source_investment_decision_id=source_investment_decision_id,
            source_recommendation_id=source_recommendation_id, opportunity_id=opportunity_id,
            buyer=buyer, problem=problem, value_proposition=value_proposition,
            product_category=product_category, primary_workflow=primary_workflow,
            differentiators=differentiators, required_capabilities=required_capabilities,
            excluded_capabilities=excluded_capabilities, experience_principles=experience_principles,
            expected_output_contract=expected_output_contract,
        )

        cur.execute(
            "SELECT id, spec_hash FROM build_spec WHERE action_request_id = %s",
            (action_request_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != spec_hash:
                raise IdempotencyConflictError(
                    f"build_spec for action {action_request_id} already exists with "
                    "materially different build intent"
                )
            return BuildSpecResult(existing[0], spec_hash, created=False)

        cur.execute(
            """
            INSERT INTO build_spec
                (venture_id, action_request_id, source_investment_decision_id,
                 source_recommendation_id, opportunity_id, buyer, problem,
                 value_proposition, product_category, primary_workflow, differentiators,
                 required_capabilities, excluded_capabilities, experience_principles,
                 expected_output_contract, spec_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (venture_id, action_request_id, source_investment_decision_id,
             source_recommendation_id, opportunity_id, buyer, problem, value_proposition,
             product_category, primary_workflow, Json(differentiators),
             Json(required_capabilities), Json(excluded_capabilities),
             Json(experience_principles), Json(expected_output_contract), spec_hash),
        )
        build_spec_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="build.build_spec_frozen", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"build_spec_id": str(build_spec_id), "spec_hash": spec_hash},
        )
    return BuildSpecResult(build_spec_id, spec_hash, created=True)


def get_build_spec(conn, action_request_id: str):
    """Return the frozen build_spec row for a BUILD action, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, action_request_id, source_investment_decision_id, "
            "source_recommendation_id, opportunity_id, buyer, problem, value_proposition, "
            "product_category, primary_workflow, differentiators, required_capabilities, "
            "excluded_capabilities, experience_principles, expected_output_contract, "
            "spec_hash, created_at FROM build_spec WHERE action_request_id = %s",
            (action_request_id,),
        )
        return cur.fetchone()
