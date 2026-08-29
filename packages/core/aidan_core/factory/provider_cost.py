"""Trusted, deterministic provider-cost derivation for capital governance.

A paid provider action reserves a FROZEN spend ceiling (the governed ActionRequest amount)
BEFORE the provider is invoked; after execution the kernel must reconcile the actually-
incurred cost and release the unused reservation. Exact provider billing is usually not
synchronously available, so this module derives a deterministic ESTIMATED cost with these
trust properties:

- pricing is KERNEL-OWNED and frozen here — a provider/worker self-reported DOLLAR cost is
  never accepted as canonical;
- the only external input is provider-reported USAGE (token counts). It can only ever
  *reduce* the committed amount below the reserved ceiling (releasing unused reservation);
  it can never raise committed above the ceiling, and it never affects the pre-execution
  reservation that already bounds capital exposure;
- missing usage, an unknown model, or malformed counts fall back to the FULL ceiling (the
  most conservative accounting), never to zero.

The result is classified so callers/audit can see it is an estimate pending real billing.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Kernel-owned frozen pricing: USD per 1,000,000 tokens (input, output). Conservative and
# deliberately static — updated only by an explicit code change, never by provider input.
FROZEN_PRICING = {
    "gpt-5-nano": (Decimal("0.05"), Decimal("0.40")),
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "gpt-5": (Decimal("1.25"), Decimal("10.00")),
    "gpt-5.3-codex": (Decimal("1.75"), Decimal("14.00")),
}

_MILLION = Decimal(1_000_000)

# Classifications (static, audit-safe).
ESTIMATED = "ESTIMATED_FROM_USAGE"          # kernel-derived from usage x frozen pricing
ESTIMATED_CAPPED = "ESTIMATED_CAPPED_AT_CEILING"
CONSERVATIVE_CEILING = "CONSERVATIVE_CEILING"   # no trusted usage/model -> full ceiling


def estimate_cost(model, usage, *, ceiling) -> tuple[Decimal, str]:
    """Return ``(estimated_cost, classification)`` bounded to ``[0, ceiling]``.

    ``usage`` is provider-reported metadata (e.g. ``{"input_tokens": int, "output_tokens":
    int}``); anything untrusted/missing yields the conservative full ceiling.
    """
    try:
        ceiling = Decimal(ceiling)
    except (InvalidOperation, TypeError):
        raise ValueError("ceiling must be a numeric amount")
    if ceiling < 0:
        raise ValueError("ceiling must be non-negative")

    price = FROZEN_PRICING.get(model)
    inp = usage.get("input_tokens") if isinstance(usage, dict) else None
    out = usage.get("output_tokens") if isinstance(usage, dict) else None
    if price is None or not isinstance(inp, int) or not isinstance(out, int) or inp < 0 or out < 0:
        return ceiling, CONSERVATIVE_CEILING   # unknown model / missing / malformed -> worst case

    est = (Decimal(inp) * price[0] + Decimal(out) * price[1]) / _MILLION
    if est >= ceiling:
        return ceiling, ESTIMATED_CAPPED
    return est, ESTIMATED
