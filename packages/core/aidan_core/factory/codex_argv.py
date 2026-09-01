"""Single source of truth for the Codex ``exec`` production argv.

Kept as a standalone, dependency-free module so BOTH the ``CodexExecWorker`` adapter and the CI
argv-falsifier (``tools/verify_codex_argv.py``) build the EXACT same command line from one place —
a hand-copied flag list in the falsifier is exactly the drift that let a malformed argv reach a
live smoke. Importing this module pulls in nothing heavy (no psycopg / DB), so the lightweight
codex-contract CI job can use it without installing the whole package.
"""
from __future__ import annotations


def build_codex_argv(bin_path: str, model: str, workspace: str) -> list:
    """The exact argv the adapter drives: prompt on stdin (``-``), JSON event stream, frozen model,
    isolated workspace, non-interactive, ephemeral, ambient user config/rules refused.

    ``--ask-for-approval`` is a GLOBAL codex option (accepted only on the root command, not on the
    ``exec`` subcommand in codex 0.151.0), so it MUST precede ``exec``. Placing it after ``exec`` is
    rejected by clap with exit 2 before any provider request — the confirmed live-smoke-#2 root
    cause, caught deterministically by tools/verify_codex_argv.py. Every other flag is an ``exec``
    option and stays after the subcommand. This argv is an adapter detail and does not feed the
    frozen execution spec_hash."""
    return [
        bin_path,
        "--ask-for-approval", "never",   # GLOBAL option -> before the subcommand
        "exec", "-", "--json",
        "--model", model,
        "--cd", workspace,
        "--sandbox", "workspace-write",
        "--ephemeral",
        "--ignore-user-config",     # do not load $CODEX_HOME/config.toml semantics from ambient config
        "--ignore-rules",           # skip user/project execpolicy .rules
        "-c", "shell_environment_policy.ignore_default_excludes=false",
    ]
