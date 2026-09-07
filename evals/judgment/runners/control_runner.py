"""Control runner for the AI-DAN Phase-1 judgment evaluation — arms C0 and C1.

Runs a single-pass control arm against a development case:
  * load the case, take ONLY the model-facing view (hidden_ground_truth is never rendered),
  * render the C0 (general) or C1 (distilled-AIDAN) prompt with the case fields,
  * call the configured model (default: zero-cost MOCK; 'live' is paid opt-in only),
  * extract the shared-schema JSON, record token/cost, return an ArmResult for the scorer.

No paid call happens unless the client is constructed in 'live' mode with an API key.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # judgment dir -> import common
import common  # noqa: E402


def run_case(arm: str, case: dict, client) -> common.ArmResult:
    assert arm in ("C0", "C1"), f"control_runner handles C0/C1, not {arm!r}"
    view = common.model_facing_view(case)  # asserts ground truth is excluded
    prompt = common.render_single_prompt(arm, case)
    cost = common.CostRecord()
    try:
        text, c = client.complete(prompt, case=view, want_json=True)
        cost.add(c)
        decision = common.extract_json(text)
        return common.ArmResult(case_id=case.get("case_id"), arm=arm, model=client.model,
                                mode=client.mode, ok=True, decision=decision, raw_text=text, cost=cost)
    except Exception as exc:  # parse failure / transport error -> recorded, scored as a miss
        return common.ArmResult(case_id=case.get("case_id"), arm=arm, model=client.model,
                                mode=client.mode, ok=False, decision=None,
                                raw_text=locals().get("text", ""), cost=cost, error=f"{type(exc).__name__}: {exc}")


def main() -> int:
    raise SystemExit("Use run_dev.py to execute all arms/cases; control_runner is a library module.")


if __name__ == "__main__":
    main()
