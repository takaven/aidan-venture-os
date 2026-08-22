"""Gate 8 live-smoke harness instrumentation tests (deterministic; no DB, no network).

Proves the smoke's bounded providers count and hard-bound the REAL production call boundaries after
the tool_use repair: ``TavilySearchAdapter.acquire`` and ``AnthropicResearchProposer._tool_call`` (the
latter covers BOTH research_questions() and propose()). An over-bound third call fails closed BEFORE
it reaches transport. No provider keys, no real calls.
"""
from __future__ import annotations

import pytest

from aidan_core.errors import InvalidAcquisitionError
from aidan_core.research.http_providers import ENV_ANTHROPIC_API_KEY, ENV_ANTHROPIC_MODEL, ENV_TAVILY_API_KEY
from aidan_core.research.live_smoke import BoundedAnthropic, BoundedTavily

_ANTH_ENV = {ENV_ANTHROPIC_API_KEY: "k", ENV_ANTHROPIC_MODEL: "m"}
_TAV_ENV = {ENV_TAVILY_API_KEY: "k"}


def _tool_resp(name, input_obj):
    return {"id": "m", "type": "message", "role": "assistant", "model": "m",
            "content": [{"type": "tool_use", "id": "t", "name": name, "input": input_obj}],
            "stop_reason": "tool_use"}


def test_bounded_anthropic_counts_both_calls_and_blocks_third_before_transport():
    counter = {"tavily": 0, "anthropic": 0}
    net = {"calls": 0}

    def transport(method, url, *, headers, body=None, timeout=30):
        net["calls"] += 1
        name = body["tool_choice"]["name"]
        if name == "emit_research_questions":
            return 200, _tool_resp(name, {"questions": ["q"]})
        return 200, _tool_resp(name, {})   # empty-but-valid proposal object
    p = BoundedAnthropic(counter, env=_ANTH_ENV, transport=transport)
    p.research_questions("m")
    assert counter["anthropic"] == 1 and net["calls"] == 1
    p.propose("m", [])
    assert counter["anthropic"] == 2 and net["calls"] == 2   # BOTH calls counted at _tool_call
    with pytest.raises(InvalidAcquisitionError):
        p.research_questions("m")                            # over-bound third call
    assert counter["anthropic"] == 3 and net["calls"] == 2   # transport NOT invoked on the blocked call


def test_bounded_tavily_counts_and_blocks_third_before_transport():
    counter = {"tavily": 0, "anthropic": 0}
    net = {"calls": 0}

    def transport(method, url, *, headers, body=None, timeout=30):
        net["calls"] += 1
        return 200, {"results": [{"url": "https://x", "content": "some source content"}]}
    a = BoundedTavily(counter, env=_TAV_ENV, transport=transport)
    a.acquire("q1")
    a.acquire("q2")
    assert counter["tavily"] == 2 and net["calls"] == 2
    with pytest.raises(InvalidAcquisitionError):
        a.acquire("q3")
    assert counter["tavily"] == 3 and net["calls"] == 2      # blocked before transport
