"""Capital-governed provider execution — deterministic (no provider calls).

Proves (1) the trusted provider-cost estimator: kernel-owned frozen pricing, bounded by the
frozen ceiling, conservative on missing/unknown data, provider self-reported DOLLAR cost
ignored; and (2) the reserve->reconcile->release flow over the EXISTING action-keyed budget
primitives, plus the capital-safety properties.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from aidan_core import budget
from aidan_core.errors import InsufficientBudgetError
from aidan_core.factory import provider_cost as pc

from conftest import setup_action

CEIL = Decimal("1.0000")


# ---- trusted estimator (pure) -----------------------------------------------

def test_estimate_from_usage_and_frozen_pricing():
    cost, cls = pc.estimate_cost("gpt-5-mini", {"input_tokens": 20_000, "output_tokens": 5_000}, ceiling=CEIL)
    # 20000*0.25/1e6 + 5000*2.00/1e6 = 0.005 + 0.010 = 0.015
    assert cost == Decimal("0.015") and cls == pc.ESTIMATED


def test_estimate_capped_at_ceiling():
    cost, cls = pc.estimate_cost("gpt-5.3-codex", {"input_tokens": 10**9, "output_tokens": 10**9}, ceiling=CEIL)
    assert cost == CEIL and cls == pc.ESTIMATED_CAPPED


@pytest.mark.parametrize("model,usage", [
    ("unknown-model", {"input_tokens": 10, "output_tokens": 10}),   # unknown model
    ("gpt-5-mini", None),                                            # missing usage
    ("gpt-5-mini", {"input_tokens": "x", "output_tokens": 5}),       # malformed
    ("gpt-5-mini", {"input_tokens": -1, "output_tokens": 5}),        # negative
    ("gpt-5-mini", {}),                                              # empty
])
def test_conservative_ceiling_when_untrusted(model, usage):
    cost, cls = pc.estimate_cost(model, usage, ceiling=CEIL)
    assert cost == CEIL and cls == pc.CONSERVATIVE_CEILING


def test_provider_self_reported_dollar_cost_is_ignored():
    # A worker/provider-declared "cost" field must not influence the derived cost.
    cost, _ = pc.estimate_cost("gpt-5-mini", {"cost": 0, "input_tokens": 20_000, "output_tokens": 5_000}, ceiling=CEIL)
    assert cost == Decimal("0.015")   # from tokens x frozen pricing, not the declared 0


def test_estimate_never_exceeds_ceiling_or_goes_negative():
    for u in ({"input_tokens": 0, "output_tokens": 0}, {"input_tokens": 10**7, "output_tokens": 10**7}):
        cost, _ = pc.estimate_cost("gpt-5", u, ceiling=CEIL)
        assert Decimal(0) <= cost <= CEIL


# ---- governed flow over existing budget primitives (DB) ---------------------

def _committed_reserved(migrated, vid, currency="USD"):
    with migrated.cursor() as cur:
        cur.execute("SELECT reserved_amount, committed_amount FROM budget_account "
                    "WHERE venture_id = %s AND currency = %s", (vid, currency))
        return cur.fetchone()


def test_reserve_estimate_reconcile_releases_unused(migrated):
    vid, aid = setup_action(migrated, slug="pc-ok", amount=CEIL, grant=Decimal("5.0000"))
    assert budget.reserve_budget(migrated, aid) is True
    assert _committed_reserved(migrated, vid) == (CEIL, Decimal("0.0000"))     # ceiling reserved
    cost, _ = pc.estimate_cost("gpt-5-mini", {"input_tokens": 20_000, "output_tokens": 5_000}, ceiling=CEIL)
    with migrated.cursor() as cur:
        budget.reconcile_completion(cur, aid, cost)
    reserved, committed = _committed_reserved(migrated, vid)
    assert committed == Decimal("0.0150") and reserved == Decimal("0.0000")    # estimated committed, rest released


def test_insufficient_budget_blocks_reservation_before_any_provider_call(migrated):
    vid, aid = setup_action(migrated, slug="pc-poor", amount=CEIL, grant=Decimal("0.5000"))
    with pytest.raises(InsufficientBudgetError):
        budget.reserve_budget(migrated, aid)     # reservation is the gate BEFORE provider execution
    assert _committed_reserved(migrated, vid) == (Decimal("0.0000"), Decimal("0.0000"))


def test_failure_releases_the_whole_reservation(migrated):
    vid, aid = setup_action(migrated, slug="pc-fail", amount=CEIL, grant=Decimal("5.0000"))
    budget.reserve_budget(migrated, aid)
    budget.release_budget(migrated, aid)         # provider error/timeout path
    assert _committed_reserved(migrated, vid) == (Decimal("0.0000"), Decimal("0.0000"))


def test_retry_cannot_reserve_beyond_remaining_budget(migrated):
    grant = Decimal("1.5000")
    vid, aid1 = setup_action(migrated, slug="pc-r1", amount=CEIL, grant=grant, key="k1")
    budget.reserve_budget(migrated, aid1)
    # A second action whose ceiling would exceed the remaining 0.5 must be refused.
    from aidan_core import actions
    aid2 = actions.submit_action_request(
        migrated, venture_id=vid, action_type="spend", actor="a", idempotency_key="k2",
        required_autonomy=0, requested_amount=CEIL, requested_currency="USD").action_id
    with pytest.raises(InsufficientBudgetError):
        budget.reserve_budget(migrated, aid2)


def test_capital_is_isolated_per_venture(migrated):
    vidA, aidA = setup_action(migrated, slug="pc-A", amount=CEIL, grant=Decimal("2.0000"), key="ka")
    vidB, aidB = setup_action(migrated, slug="pc-B", amount=CEIL, grant=Decimal("2.0000"), key="kb")
    budget.reserve_budget(migrated, aidA)
    with migrated.cursor() as cur:
        budget.reconcile_completion(cur, aidA, Decimal("0.5000"))
    # Venture B's account is untouched by venture A's spend.
    assert _committed_reserved(migrated, vidB) == (Decimal("0.0000"), Decimal("0.0000"))
