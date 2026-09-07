"""Shared plumbing for the AI-DAN Phase-1 judgment evaluation harness.

Everything the runners and scorer need in common: case loading (with the scorer-only
`hidden_ground_truth` stripped before any model call), prompt rendering, a configurable model client
(default: a zero-cost MOCK so the harness runs end-to-end without paid calls), robust JSON extraction
from model output, and small result/cost dataclasses.

NO paid model call happens unless the client mode is explicitly set to "live" AND an API key is
present. The default mode is "mock".

Self-contained: stdlib only. Does not import the production `aidan_core` package.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
JUDGMENT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = JUDGMENT_DIR / "prompts"
DEV_CASES_DIR = JUDGMENT_DIR / "cases" / "development"
HOLDOUT_CASES_DIR = JUDGMENT_DIR / "cases" / "holdout"
RESULTS_DIR = JUDGMENT_DIR / "results"

ARMS = ("C0", "C1", "T")
PROMPT_FILE = {
    "C0": PROMPTS_DIR / "c0_general.md",
    "C1": PROMPTS_DIR / "c1_distilled_aidan.md",
    "T": PROMPTS_DIR / "t_aidan_staged.md",
}

# ---------------------------------------------------------------------------
# local env files (gitignored) — a convenient place to put secrets/config for a live run.
# ---------------------------------------------------------------------------
def load_env_files() -> list[str]:
    """Load KEY=VALUE lines from gitignored `.env` then `.env.local` in this directory into the
    process environment. A REAL environment variable always wins (never overridden); among files,
    `.env.local` overrides `.env`. Values may be quoted; lines starting with '#' are comments.
    Returns the names of the files that were loaded (for diagnostics only — never prints values).
    """
    preexisting = set(os.environ)
    loaded = []
    for name in (".env", ".env.local"):
        p = JUDGMENT_DIR / name
        if not p.exists():
            continue
        loaded.append(name)
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not k or k in preexisting:   # never override a real environment variable
                continue
            os.environ[k] = v               # .env.local (loaded second) overrides .env
    return loaded


_LOADED_ENV_FILES = load_env_files()   # populate os.environ from local files BEFORE reading defaults

# Single configurable model for ALL arms (frozen-design requirement). Swap via env AIDAN_EVAL_MODEL.
DEFAULT_MODEL = os.environ.get("AIDAN_EVAL_MODEL", "claude-opus-4-8")
# Mode: "mock" (default, zero cost, no network) | "live" (real paid calls — must be set explicitly).
DEFAULT_MODE = os.environ.get("AIDAN_EVAL_MODE", "mock")

# Approximate USD per 1M tokens. APPROX ONLY — verify before relying on cost figures. Used only in
# live mode to estimate cost from returned usage; mock mode is always 0.
_APPROX_PRICE_PER_MTOK = {
    # model_id: (input_usd_per_mtok, output_usd_per_mtok)
    "claude-opus-4-8": (5.0, 25.0),      # APPROX placeholder — update to real pricing before use
    "_default": (5.0, 25.0),
}


# ---------------------------------------------------------------------------
# data types
# ---------------------------------------------------------------------------
@dataclass
class CostRecord:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Optional[float] = 0.0
    calls: int = 0

    def add(self, other: "CostRecord") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        if self.cost_usd is None or other.cost_usd is None:
            self.cost_usd = None
        else:
            self.cost_usd += other.cost_usd


@dataclass
class ArmResult:
    case_id: str
    arm: str
    model: str
    mode: str
    ok: bool                       # did we get parseable schema JSON?
    decision: Optional[dict]       # the parsed shared-schema output (None if parse failed)
    raw_text: str                  # final model text (the Stage-7 text for T)
    cost: CostRecord
    stages: list = field(default_factory=list)   # T only: intermediate stage texts
    error: Optional[str] = None

    def to_json(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# case loading + rendering (ground truth NEVER reaches the model)
# ---------------------------------------------------------------------------
MODEL_FACING_KEYS = ("mandate", "capital_ceiling_usd", "opportunities", "evidence")


def load_case(path: Path) -> dict:
    """Load the full case JSON (including hidden_ground_truth — for the scorer only)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def model_facing_view(case: dict) -> dict:
    """The subset the model is allowed to see. Asserts the ground truth is excluded."""
    view = {k: case[k] for k in MODEL_FACING_KEYS if k in case}
    view["_case_id"] = case.get("case_id")
    assert "hidden_ground_truth" not in view, "ground truth must never reach the model"
    return view


def list_dev_cases() -> list[Path]:
    return sorted(DEV_CASES_DIR.glob("dev-*.json"))


def render_opportunities(case: dict) -> str:
    lines = []
    for o in case.get("opportunities", []):
        ask = o.get("ask_usd")
        ask_s = f" (ask: USD {ask})" if ask is not None else ""
        lines.append(f"- [{o['id']}] {o.get('name','')}: {o.get('summary','')}{ask_s}")
    return "\n".join(lines)


def render_evidence(case: dict) -> str:
    """Flatten single-shot `items[]`, or concatenate staged `rounds` T0->T1->T2 (labelled).

    Both single-shot (C0/C1) and staged (T) arms see the FULL evidence; for staged cases the rounds
    are presented in order and explicitly labelled as newer-may-contradict-older.
    """
    ev = case.get("evidence", {}) or {}
    if "items" in ev:
        return "\n".join(f"- {x}" for x in ev["items"])
    if "rounds" in ev:
        out = []
        for rnd in ("T0", "T1", "T2"):
            if rnd in ev["rounds"]:
                out.append(f"[{rnd}]")
                out += [f"- {x}" for x in ev["rounds"][rnd]]
        return "\n".join(out)
    return "(no evidence)"


def _fill(template: str, case: dict, prior: str = "") -> str:
    return (template
            .replace("{{MANDATE}}", str(case.get("mandate", "")))
            .replace("{{CAPITAL_CEILING_USD}}", str(case.get("capital_ceiling_usd", "")))
            .replace("{{OPPORTUNITIES}}", render_opportunities(case))
            .replace("{{EVIDENCE}}", render_evidence(case))
            .replace("{{PRIOR}}", prior))


def _prompt_body_after_first_hr(text: str) -> str:
    """C0/C1 prompt files: the actual prompt is everything after the first `---` horizontal rule."""
    parts = re.split(r"(?m)^---\s*$", text, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else text.strip()


def render_single_prompt(arm: str, case: dict) -> str:
    """Render the C0 or C1 single-shot prompt with the case's model-facing fields filled in."""
    assert arm in ("C0", "C1")
    text = PROMPT_FILE[arm].read_text(encoding="utf-8")
    return _fill(_prompt_body_after_first_hr(text), case)


def parse_staged_prompt() -> tuple[str, list[str]]:
    """Parse t_aidan_staged.md into (shared_context_template, [stage_bodies]).

    shared_context_template is the first fenced code block (contains {{MANDATE}} etc.);
    stage_bodies are the text under each `## Stage N` header, in order (each may contain {{PRIOR}}).
    """
    text = PROMPT_FILE["T"].read_text(encoding="utf-8")
    m = re.search(r"```(?:text)?\s*\n(.*?)\n```", text, flags=re.S)
    shared = m.group(1).strip() if m else ""
    # rules line, if present, is appended to shared context for every stage
    rm = re.search(r"(?m)^(Rules for all stages:.*?)$", text)
    if rm:
        shared = shared + "\n\n" + rm.group(1).strip()
    # split stages on '## Stage N' headers
    after = text.split("## Stage", 1)
    stages = []
    if len(after) == 2:
        chunks = re.split(r"(?m)^## Stage\s+\d+", "## Stage" + after[1])
        for c in chunks:
            c = c.strip()
            if c:
                # drop a leading "N — Title" line remnant; keep the instruction body
                stages.append(c)
    return shared, stages


# ---------------------------------------------------------------------------
# model clients
# ---------------------------------------------------------------------------
class MockModelClient:
    """Zero-cost, no-network client. Returns SCHEMA-VALID but deliberately NAIVE output built only
    from the model-facing view (never the ground truth), so the harness + scorer run end-to-end
    without any paid call. Clearly labelled model='mock-baseline' so results are never mistaken for a
    real evaluation."""

    model = "mock-baseline"
    mode = "mock"

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 case: Optional[dict] = None, want_json: bool = False) -> tuple[str, CostRecord]:
        cost = CostRecord(input_tokens=0, output_tokens=0, cost_usd=0.0, calls=1)
        if not want_json:
            return ("(mock intermediate stage output)", cost)
        case = case or {}
        opps = case.get("opportunities", [])
        ceiling = float(case.get("capital_ceiling_usd") or 0)
        ids = [o["id"] for o in opps]
        # Naive fixed policy (NOT ground-truth aware): fund the largest-ask opportunity, hold the rest,
        # propose a generic cheap test, allocate up to half the ceiling to the funded one.
        funded = max(opps, key=lambda o: o.get("ask_usd") or 0)["id"] if opps else None
        decision = {
            "ranking": ids,
            "opportunities": [
                {"id": i, "decision": ("FUND" if i == funded else "HOLD"),
                 "confidence": 0.5, "rationale": "(mock baseline)",
                 "kill_reason": None, "critical_assumptions": ["(mock)"]}
                for i in ids
            ],
            "critical_unknown": "(mock) whether demand is real",
            "cheapest_discriminating_test": {
                "description": "(mock) run a small paid pilot", "est_cost_usd": min(50.0, ceiling),
                "what_it_resolves": "willingness to pay", "decision_if_pass": "fund",
                "decision_if_fail": "kill"},
            "capital_allocation": [{"id": i, "allocate_usd": (min(ceiling / 2, 100.0) if i == funded else 0)}
                                   for i in ids],
            "total_allocated_usd": (min(ceiling / 2, 100.0) if funded else 0),
            "overall_recommendation": "(mock baseline recommendation)",
            "confidence": 0.5,
        }
        return (json.dumps(decision), cost)


class AnthropicModelClient:
    """Real Anthropic Messages API client over stdlib urllib (no third-party dependency).

    LIVE / PAID. Only constructed when mode='live'. Requires ANTHROPIC_API_KEY. Reads token usage
    from the response and estimates cost from the APPROX price table above.
    """

    mode = "live"

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 4096):
        self.model = model
        self.max_tokens = max_tokens
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("live mode requires ANTHROPIC_API_KEY (none set)")

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 case: Optional[dict] = None, want_json: bool = False) -> tuple[str, CostRecord]:  # pragma: no cover - network
        import urllib.request
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        text = "".join(blk.get("text", "") for blk in data.get("content", []) if blk.get("type") == "text")
        usage = data.get("usage", {})
        cost = CostRecord(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cost_usd=estimate_cost(self.model, usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
            calls=1)
        return (text, cost)


def estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = _APPROX_PRICE_PER_MTOK.get(model, _APPROX_PRICE_PER_MTOK["_default"])
    return round((in_tok / 1e6) * pin + (out_tok / 1e6) * pout, 6)


def get_client(mode: str = DEFAULT_MODE, model: str = DEFAULT_MODEL):
    """Return a model client. mode='mock' (default, free) or 'live' (paid — explicit opt-in only)."""
    if mode == "live":
        return AnthropicModelClient(model=model)
    return MockModelClient()


# ---------------------------------------------------------------------------
# robust JSON extraction from model output
# ---------------------------------------------------------------------------
class OutputParseError(Exception):
    pass


def extract_json(text: str) -> dict:
    """Extract the shared-schema JSON object from model text, tolerating fences / surrounding prose."""
    text = (text or "").strip()
    # 1) whole thing is JSON
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) ```json ... ``` or ``` ... ``` fenced block
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) first balanced { ... } object
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
    raise OutputParseError("no parseable JSON object in model output")


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
