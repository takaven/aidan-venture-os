"""Control runner for the AI-DAN Phase-1 judgment evaluation — arms C0 and C1.

STATUS: SKELETON / PLACEHOLDER. No real logic yet. See ../README.md for the frozen design.

This runner will execute the two single-pass control arms against a case's FIXED FROZEN evidence
pack, using the SAME model as every other arm, and emit each arm's decision in the shared
machine-readable output schema plus per-case token/cost telemetry.

Hard rules (frozen design):
  * same model + decoding params as the treatment runner;
  * evidence comes ONLY from the case's frozen pack — NO live research, web, or tools;
  * one pass per arm (C0 general, C1 distilled-AIDAN);
  * record tokens/cost per case (required for the cost-adjusted comparison);
  * never read holdout answer keys during development.
"""
from __future__ import annotations

# TODO: shared types (Case, ArmResult, CostRecord) — factor out into a small module shared with
#       treatment_runner.py once implemented.
# TODO: load_case(path) -> Case  (frozen evidence pack + metadata; NEVER the answer key at run time)
# TODO: render_prompt(arm, case) using prompts/c0_general.md / prompts/c1_distilled_aidan.md
# TODO: call_model(prompt) -> (raw_output, cost)  — single model client, no network research tools
# TODO: parse_decision(raw_output) -> structured decision in the shared output schema
# TODO: run_arm(arm in {"C0","C1"}, case) -> ArmResult
# TODO: main(): iterate cases/development (or a passed set), write results as JSON for scorer/score.py


def main() -> int:
    raise NotImplementedError(
        "control_runner is a skeleton; implement per evals/judgment/README.md before running")


if __name__ == "__main__":
    raise SystemExit(main())
