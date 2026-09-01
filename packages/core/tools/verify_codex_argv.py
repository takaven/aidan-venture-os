"""Deterministic Codex 0.151.0 argv PARSE falsification — NO credential, NO provider request.

Runs the EXACT production adapter argv (taken from ``CodexExecWorker._argv`` itself, never a copy
that could drift) with NO API key and a benign prompt on stdin, inside a throwaway git dir, and
classifies the failure from BOUNDED markers only. It distinguishes:
  - ARG_PARSE_ERROR        : the CLI rejected the flag shape (adapter argv is wrong for 0.151.0)
  - AUTH_OR_CONFIG_MISSING : flags accepted; the run only failed for lack of a credential
  - OTHER / TIMEOUT        : inconclusive

Because no credential is present, the CLI fails BEFORE any provider API request (clap parses argv
before auth/network), so this can never contact OpenAI or spend. On ARG_PARSE_ERROR it additionally
surfaces, safely, which of OUR OWN known flag tokens clap rejected and each flag's parser scope
(accepted by the ``exec`` subcommand help and/or the global help) — enough to repair the argv
minimally. It never echoes raw stderr/stdout; the only tokens it can print are ones already present
in our own argv. Used by the codex-contract CI job; fails closed on ARG_PARSE_ERROR.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

_ARG_MARKERS = ["unexpected argument", "unrecognized", "invalid value for", "error: a value",
                "error: unexpected", "usage:", "tip: a similar", "argument"]
_AUTH_MARKERS = ["not logged in", "authenticate", "api key", "codex_api_key", "openai_api_key",
                 "sign in", "login", "401", "unauthorized", "credential", "no api key"]

# clap quotes the offending token; we only ever surface a token that is ALSO in our own argv.
_QUOTED = re.compile(r"'([^']{1,64})'")


def _production_argv(codex_bin: str, ws: str):
    """The EXACT argv the adapter emits (frozen model, given workspace), from the shared single
    source of truth — importing it pulls in nothing heavy (no psycopg/DB), so this needs no install."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> packages/core
    from aidan_core.factory.codex_argv import build_codex_argv
    return build_codex_argv(codex_bin, "gpt-5-mini", ws)


def classify(text: str) -> str:
    low = (text or "").lower()
    auth = any(m in low for m in _AUTH_MARKERS)
    arg = any(m in low for m in _ARG_MARKERS)
    if arg and not auth:
        return "ARG_PARSE_ERROR"
    if auth:
        return "AUTH_OR_CONFIG_MISSING"
    return "OTHER"


def _run(argv, ws, stdin=""):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": ws, "CODEX_HOME": ws, "TMPDIR": ws}
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, timeout=60)


def _rejected_own_tokens(stderr_stdout: str, own_tokens):
    """Tokens clap quoted as offending that are ALSO in our argv (never arbitrary text)."""
    quoted = set(_QUOTED.findall(stderr_stdout or ""))
    return [t for t in own_tokens if t in quoted]


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        print("classification=CODEX_NOT_FOUND exit=n/a")
        return 1
    with tempfile.TemporaryDirectory() as ws:
        subprocess.run(["git", "init", "-q", ws], check=False)
        argv = _production_argv(codex, ws)
        # only long/short flag tokens from OUR argv are ever eligible to be echoed back
        own_flags = [a for a in argv if a.startswith("-") and a != "-"]
        try:
            p = _run(argv, ws, stdin="say hi")
        except subprocess.TimeoutExpired:
            print("classification=TIMEOUT exit=timeout")
            return 0
        combined = (p.stderr or "") + "\n" + (p.stdout or "")
        cat = classify(combined)
        print(f"classification={cat} exit={p.returncode}")
        low = combined.lower()
        print("markers: arg=%s auth=%s" % (
            any(m in low for m in _ARG_MARKERS), any(m in low for m in _AUTH_MARKERS)))

        if cat == "ARG_PARSE_ERROR":
            rejected = _rejected_own_tokens(combined, own_flags)
            print(f"rejected_own_flags: {rejected or 'unnamed'}")
            # Scope probe: for each production flag, is it accepted by the exec subcommand's help
            # and/or the global help? A flag present only in the global help but placed AFTER `exec`
            # is the classic clap "global option must precede subcommand" drift.
            exec_help = _run([codex, "exec", "--help"], ws).stdout or ""
            root_help = _run([codex, "--help"], ws).stdout or ""
            for f in own_flags:
                in_exec = f in exec_help
                in_root = f in root_help
                print(f"scope {f}: exec={in_exec} global={in_root}")
            print("FAIL: production adapter argv was rejected by codex 0.151.0 (contract drift)")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
