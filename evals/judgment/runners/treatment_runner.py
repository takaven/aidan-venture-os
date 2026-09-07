"""Treatment runner for the AI-DAN Phase-1 judgment evaluation — arm T (AIDAN staged).

STATUS: SKELETON / PLACEHOLDER. No real logic yet. See ../README.md for the frozen design.

This runner will execute the full AIDAN staged decision process against a case's FIXED FROZEN evidence
pack, using the SAME model as the control runner, and emit T's decision in the SAME shared output
schema plus per-case token/cost telemetry aggregated across ALL stages (the cost the simplicity kill
test weighs against C1).

Hard rules (frozen design):
  * same model + decoding params as the control runner;
  * evidence comes ONLY from the case's frozen pack — NO live research, web, or tools;
  * staged process (evidence intake -> observations/claims -> interpretation/assumptions -> Kill Case
    -> capital decision -> next-best-action), per prompts/t_aidan_staged.md;
  * record tokens/cost across every stage;
  * never read holdout answer keys during development.
"""
from __future__ import annotations

# TODO: reuse the shared types (Case, ArmResult, CostRecord) from the control runner's module.
# TODO: load_case(path) -> Case  (frozen evidence pack + metadata; NEVER the answer key at run time)
# TODO: STAGES = [...]  ordered stage definitions sourced from prompts/t_aidan_staged.md
# TODO: run_stage(stage, state, case) -> (stage_output, cost)  — single model client, no research tools
# TODO: run_treatment(case) -> ArmResult  — thread state across stages, sum cost across stages
# TODO: main(): iterate cases/development (or a passed set), write results as JSON for scorer/score.py


def main() -> int:
    raise NotImplementedError(
        "treatment_runner is a skeleton; implement per evals/judgment/README.md before running")


if __name__ == "__main__":
    raise SystemExit(main())
