"""Deterministic Codex CLI contract proof — NO authentication, NO model invocation.

Installs/uses an actual ``codex`` binary and verifies that the exact command surface the
``CodexExecWorker`` adapter depends on really exists in the current CLI (fail-closed on
capability drift). Runs only ``codex --version`` and ``--help`` (never ``codex exec`` against
a model). Records the CLI version. Exit 0 iff every required token is present.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

# Tokens the adapter's locked argv depends on. They must appear in the CLI's own help.
REQUIRED = [
    "exec",
    "--json",
    "--model",
    "--cd",
    "--sandbox",
    "workspace-write",
    "--ask-for-approval",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
]


def _run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"<error running {args}: {type(exc).__name__}>"


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        print("FAIL: codex CLI not found on PATH")
        return 1
    version = _run([codex, "--version"]).strip().splitlines()[0] if _run([codex, "--version"]).strip() else "?"
    print(f"codex binary: {codex}")
    print(f"codex version: {version}")

    help_text = "\n".join([
        _run([codex, "--help"]),
        _run([codex, "exec", "--help"]),
    ])
    missing = [tok for tok in REQUIRED if tok not in help_text]
    for tok in REQUIRED:
        print(f"  {'OK  ' if tok not in missing else 'MISSING'} {tok}")
    if missing:
        print(f"\nFAIL: required CLI surface missing: {missing}")
        return 1
    print("\nPASS: codex CLI exposes the adapter-required command surface "
          f"(version {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
