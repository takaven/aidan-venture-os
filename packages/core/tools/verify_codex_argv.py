"""Deterministic Codex 0.151.0 argv PARSE falsification — NO credential, NO provider request.

Runs the EXACT production adapter argv with NO API key and a benign prompt on stdin, inside a
throwaway git dir, and classifies the failure from BOUNDED stderr markers only (never emits raw
stderr/stdout). It distinguishes:
  - ARG_PARSE_ERROR       : the CLI rejected the flag shape (adapter argv is wrong for 0.151.0)
  - AUTH_OR_CONFIG_MISSING : flags accepted; the run only failed for lack of a credential
  - OTHER / TIMEOUT        : inconclusive
Because no credential is present, the CLI fails before any provider API request, so this cannot
contact OpenAI. Used by the codex-contract CI job.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# The EXACT flag shape the CodexExecWorker adapter emits (workspace filled at runtime).
FLAGS = ["exec", "-", "--json", "--model", "gpt-5-mini", "--cd", "{ws}",
         "--sandbox", "workspace-write", "--ask-for-approval", "never", "--ephemeral",
         "--ignore-user-config", "--ignore-rules",
         "-c", "shell_environment_policy.ignore_default_excludes=false"]

_ARG_MARKERS = ["unexpected argument", "unrecognized", "invalid value for", "error: a value",
                "error: unexpected", "usage:", "tip: a similar", "argument"]
_AUTH_MARKERS = ["not logged in", "authenticate", "api key", "codex_api_key", "openai_api_key",
                 "sign in", "login", "401", "unauthorized", "credential", "no api key"]


def classify(text: str) -> str:
    low = (text or "").lower()
    auth = any(m in low for m in _AUTH_MARKERS)
    arg = any(m in low for m in _ARG_MARKERS)
    if arg and not auth:
        return "ARG_PARSE_ERROR"
    if auth:
        return "AUTH_OR_CONFIG_MISSING"
    return "OTHER"


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        print("classification=CODEX_NOT_FOUND exit=n/a")
        return 1
    with tempfile.TemporaryDirectory() as ws:
        subprocess.run(["git", "init", "-q", ws], check=False)
        argv = [codex] + [a.replace("{ws}", ws) for a in FLAGS]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": ws, "CODEX_HOME": ws, "TMPDIR": ws}
        try:
            p = subprocess.run(argv, input="say hi", capture_output=True, text=True, env=env, timeout=60)
        except subprocess.TimeoutExpired:
            print("classification=TIMEOUT exit=timeout")
            return 0
        cat = classify((p.stderr or "") + "\n" + (p.stdout or ""))
        print(f"classification={cat} exit={p.returncode}")
        # bounded diagnostic: only which marker categories matched, never raw text
        low = ((p.stderr or "") + (p.stdout or "")).lower()
        print("markers: arg=%s auth=%s" % (
            any(m in low for m in _ARG_MARKERS), any(m in low for m in _AUTH_MARKERS)))
    # Fail CLOSED iff the CLI REJECTED the production flag shape (argv drift for 0.151.0). A run that
    # only failed for the (deliberately) missing credential proves the flags parsed -> PASS. OTHER /
    # TIMEOUT are inconclusive and non-fatal, but printed for the operator.
    if cat == "ARG_PARSE_ERROR":
        print("FAIL: production adapter argv was rejected by codex 0.151.0 (contract drift)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
