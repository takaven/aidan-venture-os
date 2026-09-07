"""Treatment runner for the AI-DAN Phase-1 judgment evaluation — arm T (AIDAN staged).

Runs the full staged AIDAN sequence against a development case:
  * load the case, take ONLY the model-facing view (hidden_ground_truth is never rendered),
  * parse t_aidan_staged.md into a shared context block + ordered stage bodies,
  * call the model once per stage, threading each stage's output into the next as {{PRIOR}},
  * only the FINAL stage is asked for the shared-schema JSON (the scored artifact),
  * sum token/cost across ALL stages (this total is what the simplicity kill test weighs vs C1),
  * return an ArmResult (with intermediate stage texts retained for inspection).

No paid call happens unless the client is constructed in 'live' mode with an API key.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # judgment dir -> import common
import common  # noqa: E402


def run_case(case: dict, client) -> common.ArmResult:
    view = common.model_facing_view(case)  # asserts ground truth is excluded
    shared_tmpl, stage_bodies = common.parse_staged_prompt()
    shared = common._fill(shared_tmpl, case)
    cost = common.CostRecord()
    prior_parts: list[str] = []
    stages_out: list[dict] = []
    final_text = ""
    try:
        for idx, body in enumerate(stage_bodies):
            is_final = idx == len(stage_bodies) - 1
            prior = "\n\n".join(prior_parts)
            stage_prompt = shared + "\n\n" + common._fill(body, case, prior=prior)
            text, c = client.complete(stage_prompt, case=view, want_json=is_final)
            cost.add(c)
            stages_out.append({"stage": idx + 1, "text": text})
            prior_parts.append(f"[Stage {idx + 1} output]\n{text}")
            final_text = text
        decision = common.extract_json(final_text)
        return common.ArmResult(case_id=case.get("case_id"), arm="T", model=client.model,
                                mode=client.mode, ok=True, decision=decision, raw_text=final_text,
                                cost=cost, stages=stages_out)
    except Exception as exc:
        return common.ArmResult(case_id=case.get("case_id"), arm="T", model=client.model,
                                mode=client.mode, ok=False, decision=None, raw_text=final_text,
                                cost=cost, stages=stages_out, error=f"{type(exc).__name__}: {exc}")


def main() -> int:
    raise SystemExit("Use run_dev.py to execute all arms/cases; treatment_runner is a library module.")


if __name__ == "__main__":
    main()
