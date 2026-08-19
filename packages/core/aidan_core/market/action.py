"""Immutable market action specifications (Gate 7 Slice 1).

A ``market_action_spec`` freezes the EXACT executable market intent that an OPERATING
venture is authorized to perform: channel, audience, exact content, offer/price, and
the authorized spend for this one action. It is 1:1 with the market ActionRequest and
immutable.

It does NOT duplicate the Gate-3 commercial hypothesis: success/kill criteria, WTP
modality, and the experiment-level ``max_spend`` remain owned by ``validation_test``
(referenced by FK). The kernel proves this action's ``authorized_spend_amount`` is
bounded BY that validation test's ``max_spend`` AND by canonical budget state — it does
not copy those values. Generated content/offers are proposals until frozen here; a
worker may never substitute channel/audience/content/price after authorization.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import (
    IdempotencyConflictError,
    InconsistentCanonicalStateError,
    MarketAuthorityError,
    NotFoundError,
)

MARKET_ACTION_TYPE = "send_outreach"


@dataclass(frozen=True)
class MarketActionResult:
    market_action_spec_id: str
    action_spec_hash: str
    content_hash: str
    created: bool


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_action_spec_hash(
    *, venture_id, action_request_id, opportunity_id, validation_test_id, channel_kind,
    audience_ref, audience_provenance, content_hash, offer_ref, price_amount, price_currency,
    offer_terms, authorized_spend_amount, spend_currency,
) -> str:
    """Deterministic identity over the COMPLETE immutable market execution intent."""
    return canonical_payload_hash({
        "venture_id": str(venture_id),
        "action_request_id": str(action_request_id),
        "opportunity_id": str(opportunity_id),
        "validation_test_id": str(validation_test_id),
        "channel_kind": channel_kind,
        "audience_ref": audience_ref,
        "audience_provenance": audience_provenance,
        "content_hash": content_hash,
        "offer_ref": offer_ref,
        "price_amount": None if price_amount is None else str(price_amount),
        "price_currency": price_currency,
        "offer_terms": offer_terms,
        "authorized_spend_amount": str(authorized_spend_amount),
        "spend_currency": spend_currency,
    })


def _available_budget(cur, venture_id, currency) -> Decimal:
    cur.execute(
        "SELECT granted_amount - reserved_amount - committed_amount FROM budget_account "
        "WHERE venture_id = %s AND currency = %s", (venture_id, currency))
    row = cur.fetchone()
    return Decimal(row[0]) if row is not None else Decimal(0)


def create_market_action_spec(
    conn,
    action_request_id: str,
    *,
    opportunity_id: str,
    validation_test_id: str,
    channel_kind: str,
    audience_ref: str,
    content: str,
    offer_ref: Optional[str] = None,
    price_amount=None,
    price_currency: Optional[str] = None,
    offer_terms: Optional[str] = None,
    audience_provenance: Optional[dict[str, Any]] = None,
    authorized_spend_amount=0,
    spend_currency: str = "USD",
    actor: str = "market",
) -> MarketActionResult:
    """Freeze the immutable market_action_spec for an OPERATING venture's market action.

    Idempotent per action; any materially changed intent conflicts. The venture must be
    OPERATING, the action must be a canonical market action with genuine Gate-2/3
    provenance, and the authorized spend must be bounded by the validation test's limit
    and by canonical available budget.
    """
    if not content or not str(content).strip():
        raise ValueError("content is required (exact frozen market message)")
    if not channel_kind or not str(channel_kind).strip():
        raise ValueError("channel_kind is required")
    if not audience_ref or not str(audience_ref).strip():
        raise ValueError("audience_ref is required")
    audience_provenance = audience_provenance or {}
    authorized = Decimal(str(authorized_spend_amount))
    if authorized < 0:
        raise ValueError("authorized_spend_amount must be >= 0")
    price_amount = None if price_amount is None else Decimal(str(price_amount))

    with db.transaction(conn) as cur:
        cur.execute(
            "SELECT venture_id, action_type, requested_amount, requested_currency "
            "FROM action_request WHERE id = %s", (action_request_id,))
        arow = cur.fetchone()
        if arow is None:
            raise NotFoundError(f"action_request {action_request_id} does not exist")
        venture_id, action_type, requested_amount, requested_currency = arow
        if action_type != MARKET_ACTION_TYPE:
            raise MarketAuthorityError(
                f"action {action_request_id} is {action_type!r}, not {MARKET_ACTION_TYPE!r}; "
                "only a market action may own a market_action_spec")

        cur.execute("SELECT lifecycle_state FROM venture WHERE id = %s", (venture_id,))
        state = cur.fetchone()[0]
        if state != "OPERATING":
            raise MarketAuthorityError(
                f"venture is {state}, not OPERATING; a market action requires an OPERATING venture")

        # Commercial provenance: validation_test belongs to the venture and to the exact
        # opportunity (via its hypothesis). No new commercial thesis is invented here.
        cur.execute(
            "SELECT vt.venture_id, vt.max_spend, vh.opportunity_id "
            "FROM validation_test vt JOIN validation_hypothesis vh "
            "  ON vh.id = vt.validation_hypothesis_id "
            "WHERE vt.id = %s", (validation_test_id,))
        vrow = cur.fetchone()
        if vrow is None:
            raise NotFoundError(f"validation_test {validation_test_id} does not exist")
        vt_venture, vt_max_spend, vt_opportunity = vrow
        if str(vt_venture) != str(venture_id):
            raise InconsistentCanonicalStateError("validation_test belongs to a different venture")
        if str(vt_opportunity) != str(opportunity_id):
            raise InconsistentCanonicalStateError("opportunity_id does not match the validation test's opportunity")

        # Spend authority: bounded by the validation test's precommitted max_spend AND by
        # canonical available budget; consistent with the governed action's requested amount.
        if authorized != Decimal(str(requested_amount)):
            raise MarketAuthorityError("authorized_spend_amount must equal the ActionRequest requested_amount")
        if spend_currency != requested_currency:
            raise MarketAuthorityError("spend_currency must equal the ActionRequest currency")
        if vt_max_spend is None:
            if authorized > 0:
                raise MarketAuthorityError("validation test has no precommitted max_spend; only zero-spend allowed")
        elif authorized > Decimal(vt_max_spend):
            raise MarketAuthorityError(
                f"authorized spend {authorized} exceeds validation test max_spend {vt_max_spend}")
        if authorized > _available_budget(cur, venture_id, spend_currency):
            raise MarketAuthorityError("authorized spend exceeds canonical available budget")

        content_hash = _content_hash(content)
        action_spec_hash = compute_action_spec_hash(
            venture_id=venture_id, action_request_id=action_request_id, opportunity_id=opportunity_id,
            validation_test_id=validation_test_id, channel_kind=channel_kind, audience_ref=audience_ref,
            audience_provenance=audience_provenance, content_hash=content_hash, offer_ref=offer_ref,
            price_amount=price_amount, price_currency=price_currency, offer_terms=offer_terms,
            authorized_spend_amount=authorized, spend_currency=spend_currency)

        cur.execute("SELECT id, action_spec_hash FROM market_action_spec WHERE action_request_id = %s",
                    (action_request_id,))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != action_spec_hash:
                raise IdempotencyConflictError(
                    f"market_action_spec for action {action_request_id} already exists with "
                    "materially different intent")
            return MarketActionResult(existing[0], action_spec_hash, content_hash, created=False)

        cur.execute(
            """
            INSERT INTO market_action_spec
                (venture_id, action_request_id, opportunity_id, validation_test_id, channel_kind,
                 audience_ref, audience_provenance, content, content_hash, offer_ref, price_amount,
                 price_currency, offer_terms, authorized_spend_amount, spend_currency, action_spec_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venture_id, action_request_id, opportunity_id, validation_test_id, channel_kind,
             audience_ref, Json(audience_provenance), content, content_hash, offer_ref, price_amount,
             price_currency, offer_terms, authorized, spend_currency, action_spec_hash))
        spec_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="market.action_spec_frozen", actor=actor, venture_id=venture_id,
            action_id=action_request_id,
            payload={"market_action_spec_id": str(spec_id), "action_spec_hash": action_spec_hash})
    return MarketActionResult(spec_id, action_spec_hash, content_hash, created=True)


def get_market_action_spec(conn, action_request_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, action_request_id, opportunity_id, validation_test_id, channel_kind, "
            "audience_ref, audience_provenance, content, content_hash, offer_ref, price_amount, "
            "price_currency, offer_terms, authorized_spend_amount, spend_currency, action_spec_hash, created_at "
            "FROM market_action_spec WHERE action_request_id = %s", (action_request_id,))
        return cur.fetchone()
