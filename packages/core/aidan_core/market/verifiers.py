"""Deterministic market-action verifier (Gate 7 Slice 2).

A ``MarketActionVerifier`` is an ordinary Gate-4 ``Verifier`` (no DB, no lifecycle/spec
mutation). It independently reads the controlled channel outbox and proves that the EXACT
authorized action occurred — a worker's ``sent=true`` claim is inert. VERIFIED only when
EVERY required check passes (no score). The trusted kernel converts a VERIFIED result into
the ONE canonical ``proof_receipt`` (verification_type ``MARKET_ACTION``).

Load-bearing boundary: this proves the ACTION was accepted/executed by the controlled
channel. It does NOT prove any market OUTCOME (delivered/read/replied/converted) — those
are separate ``market_observation`` evidence.
"""
from __future__ import annotations

from typing import Any

from ..actions import canonical_payload_hash
from ..factory.verifiers import VerificationRequest, VerificationResult, VerifierRegistry
from . import channels as channels_mod

MARKET_ACTION = "MARKET_ACTION"


class MarketActionVerifier:
    """VERIFIED iff the controlled channel holds the exact authorized outbound action."""

    kind = "market-action"
    verification_type = MARKET_ACTION

    def verify(self, request: VerificationRequest) -> VerificationResult:
        c: dict[str, Any] = dict((request.expected_output_contract or {}).get("market", {}))
        outbox = c.get("outbox_path")
        env = channels_mod.read_envelope(outbox) if outbox else None
        accept = channels_mod.read_acceptance(outbox) if outbox else None
        expected_accept = channels_mod.acceptance_id(c.get("source_instance_ref"), request.execution_attempt_id)

        checks = []
        exists = env is not None and accept is not None
        checks.append(("ACTION_EXISTS", exists))
        if exists:
            checks.append(("ACTION_IDENTITY",
                           channels_mod.content_sha(str(env.get("content", ""))) == c.get("content_hash")
                           and str(env.get("action_spec_hash")) == str(c.get("action_spec_hash"))))
            checks.append(("AUDIENCE_IDENTITY", str(env.get("audience_ref")) == str(c.get("audience_ref"))))
            checks.append(("CHANNEL_IDENTITY",
                           str(env.get("channel_kind")) == str(c.get("channel_kind"))
                           and str(env.get("source_instance_ref")) == str(c.get("source_instance_ref"))))
            checks.append(("ACCEPTANCE_IDENTITY", accept == expected_accept))
        else:
            for name in ("ACTION_IDENTITY", "AUDIENCE_IDENTITY", "CHANNEL_IDENTITY", "ACCEPTANCE_IDENTITY"):
                checks.append((name, False))

        ok = all(v for _n, v in checks)
        evidence = {
            "market_action_spec_id": c.get("market_action_spec_id"),
            "action_spec_hash": c.get("action_spec_hash"),
            "source_instance_ref": c.get("source_instance_ref"),
            "acceptance_id": accept,
            "checks": [[n, bool(v)] for n, v in checks],
        }
        return VerificationResult(
            self.kind, "VERIFIED" if ok else "REJECTED", self.verification_type,
            canonical_payload_hash({"attempt": str(request.execution_attempt_id), **evidence}),
            detail={"checks": [{"name": n, "result": "PASS" if v else "FAIL"} for n, v in checks]})


def market_verifier_registry() -> VerifierRegistry:
    reg = VerifierRegistry()
    reg.register(MarketActionVerifier())
    return reg
