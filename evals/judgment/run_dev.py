"""Top-level runner for the AI-DAN Phase-1 judgment evaluation (development set).

Runs all three arms (C0, C1, T) on all development cases, scores them against each case's
hidden_ground_truth, writes raw outputs + scored results into results/, and prints a concise
C0 vs C1 vs T comparison.

Default is MODE=mock (zero cost, no network) so the whole pipeline can be exercised without any paid
call. Live/paid runs are explicit opt-in only:

    # default: free mock smoke of the full pipeline
    python evals/judgment/run_dev.py

    # paid live run (only when you intend to spend) — same model for all arms
    AIDAN_EVAL_MODE=live ANTHROPIC_API_KEY=sk-... python evals/judgment/run_dev.py --mode live

Flags: --mode {mock,live}  --model <id>  --arms C0,C1,T  --out <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

JUDGMENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(JUDGMENT_DIR))

import common  # noqa: E402
from runners import control_runner, treatment_runner  # noqa: E402
from scorer import score  # noqa: E402


def run_all(mode: str, model: str, arms: list[str], out_dir: Path) -> dict:
    client = common.get_client(mode=mode, model=model)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    all_case_scores: list[dict] = []
    cases = common.list_dev_cases()
    if not cases:
        raise SystemExit(f"no development cases found in {common.DEV_CASES_DIR}")

    for case_path in cases:
        case = common.load_case(case_path)
        for arm in arms:
            if arm in ("C0", "C1"):
                res = control_runner.run_case(arm, case, client)
            elif arm == "T":
                res = treatment_runner.run_case(case, client)
            else:
                raise SystemExit(f"unknown arm {arm!r}")
            rj = res.to_json()
            (raw_dir / f"{case['case_id']}__{arm}.json").write_text(
                json.dumps(rj, indent=2), encoding="utf-8")
            all_case_scores.append(score.score_case(rj, case))

    report = score.build_report(all_case_scores)
    report["run"] = {"mode": mode, "model": model, "arms": arms, "at": common.utc_stamp(),
                     "n_cases": len(cases)}
    (out_dir / "scored.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: dict) -> str:
    r = report["run"]
    lines = [f"# Judgment eval — development report ({r['at']})", "",
             f"- mode: **{r['mode']}**  model: `{r['model']}`  cases: {r['n_cases']}  arms: {', '.join(r['arms'])}",
             "", "> No PASS/AMBIGUOUS/FAIL thresholds applied — development numbers first, then "
             "pre-register thresholds before holdout.", "",
             "## Arm summary", "",
             "| Arm | Mean weighted score | OK/cases | Calls | In tok | Out tok | Cost (USD) |",
             "|---|---|---|---|---|---|---|"]
    for arm in report["arms"]:
        s = report["arms"][arm]["summary"]
        cost = "n/a" if s["cost_usd"] is None else f"{s['cost_usd']:.6f}"
        lines.append(f"| {arm} | {s['mean_weighted_score']:.3f} | {s['n_ok']}/{s['n_cases']} | "
                     f"{s['calls']} | {s['input_tokens']} | {s['output_tokens']} | {cost} |")
    lines += ["", "## Per-metric mean (by arm)", "",
              "| Metric | " + " | ".join(report["arms"]) + " |",
              "|---|" + "|".join(["---"] * len(report["arms"])) + "|"]
    for k in score.WEIGHTS:
        row = [f"{report['arms'][a]['summary']['per_metric_mean'][k]:.3f}" for a in report["arms"]]
        lines.append(f"| {k} ({score.WEIGHTS[k]}%) | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def print_summary(report: dict) -> None:
    r = report["run"]
    print(f"\n=== Judgment eval (development) - mode={r['mode']} model={r['model']} "
          f"cases={r['n_cases']} ===")
    print(f"{'arm':<4} {'mean_score':>10} {'ok':>7} {'calls':>6} {'cost_usd':>10}")
    for arm in report["arms"]:
        s = report["arms"][arm]["summary"]
        cost = "n/a" if s["cost_usd"] is None else f"{s['cost_usd']:.5f}"
        print(f"{arm:<4} {s['mean_weighted_score']:>10.3f} {s['n_ok']}/{s['n_cases']:<5} "
              f"{s['calls']:>6} {cost:>10}")
    if r["mode"] == "mock":
        print("\n(NOTE: mode=mock - outputs are a naive baseline, NOT a real evaluation. Use "
              "--mode live with an API key when you intend to spend.)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the development judgment evaluation (C0/C1/T).")
    ap.add_argument("--mode", default=common.DEFAULT_MODE, choices=["mock", "live"])
    ap.add_argument("--model", default=common.DEFAULT_MODEL)
    ap.add_argument("--arms", default="C0,C1,T")
    ap.add_argument("--out", default=str(common.RESULTS_DIR))
    args = ap.parse_args()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    report = run_all(args.mode, args.model, arms, Path(args.out))
    print_summary(report)
    print(f"\nwrote: {Path(args.out) / 'scored.json'} , {Path(args.out) / 'report.md'} , "
          f"{Path(args.out) / 'raw'}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
