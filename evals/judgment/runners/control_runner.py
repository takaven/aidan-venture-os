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

# Case schema (see cases/development/README.md): model-facing fields are `mandate`,
# `capital_ceiling_usd`, `opportunities` ({id,name,summary,ask_usd}), and `evidence`
# (single-shot `items[]` OR staged `rounds.{T0,T1,T2}`). The scorer-only `hidden_ground_truth` key
# MUST be stripped before any model call.
#
# TODO: shared types (Case, ArmResult, CostRecord) — factor out into a small module shared with
#       treatment_runner.py once implemented.
# TODO: load_case(path) -> Case  (parse JSON; keep `hidden_ground_truth` OUT of any model-facing view)
# TODO: model_facing_view(case) -> dict  (drops hidden_ground_truth; asserts the key is absent)
# TODO: render_evidence(case) -> str  (flatten `items[]`, or concatenate rounds T0->T1->T2 labelled)
# TODO: render_prompt(arm, case) -> str  (fill {{MANDATE}}, {{CAPITAL_CEILING_USD}}, {{OPPORTUNITIES}},
#       {{EVIDENCE}} into prompts/c0_general.md (C0) or prompts/c1_distilled_aidan.md (C1); single pass)
# TODO: call_model(prompt) -> (raw_output, cost)  — single model client, no network/research tools
# TODO: parse_decision(raw_output) -> dict  (the STRICT shared output JSON: ranking, opportunities[
#       {id,decision,confidence,rationale,kill_reason,critical_assumptions}], critical_unknown,
#       cheapest_discriminating_test, capital_allocation, total_allocated_usd, overall_recommendation,
#       confidence)
# TODO: run_arm(arm in {"C0","C1"}, case) -> ArmResult  (decision + CostRecord)
# TODO: main(): iterate cases/development (or a passed set), write results as JSON for scorer/score.py


def main() -> int:
    raise NotImplementedError(
        "control_runner is a skeleton; implement per evals/judgment/README.md before running")


if __name__ == "__main__":
    raise SystemExit(main())
