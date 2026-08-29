"""Provider-neutral coding worker adapter for the OpenAI Codex CLI (``codex exec``).

A replaceable :class:`~aidan_core.factory.workers.WorkerAdapter`. It receives a bounded
``WorkerRequest`` (no DB, no canonical authority), drives ``codex exec`` non-interactively
in an isolated venture workspace, and returns a ``WorkerResult`` — a CLAIM plus captured
candidate artifacts. Whether the produced code is CORRECT is decided later and only by the
deterministic ``TestExecutionVerifier`` (behavioural Bubblewrap execution) — never by this
adapter, the worker's self-report, or the provider's own success signal.

Locked contract (OpenAI Codex docs, 2026-08):
  argv:  codex exec - --json --model <FROZEN> --cd <WORKSPACE> --sandbox workspace-write
             --ask-for-approval never --skip-git-repo-check --ephemeral
             -c shell_environment_policy.ignore_default_excludes=false
  prompt: delivered on STDIN (the ``-`` positional), never on argv;
  auth:  OPENAI_API_KEY, provided ONLY in the child process environment (never argv/log/
         result). ``ignore_default_excludes=false`` makes Codex withhold KEY/SECRET/TOKEN
         vars from any tool/shell it spawns.

Security posture: the child environment is CONSTRUCTED minimally (never ``os.environ.copy()``
then scrubbed), so no unrelated host secret can leak to Codex. The adapter owns the
subprocess timeout and kills the whole process tree, raising ``WorkerTimeoutError`` (→ the
factory records a canonical ``TIMEOUT``); it performs zero retries of its own. No real
Codex process is required to test this module — the process boundary is an injectable seam.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..build import workspace as ws
from ..errors import WorkerTimeoutError
from .workers import WorkerRequest, WorkerResult

WORKER_KIND = "codex-exec"
WORKER_VERSION = "1"

# AIDAN-side host configuration (never the raw provider var names in code paths that log).
_API_KEY_ENV = "WORKER_OPENAI_API_KEY"        # host provides the key here; passed to child as OPENAI_API_KEY
_BIN_ENV = "WORKER_CODEX_BIN"                  # optional explicit path to the codex binary

_MAX_ARTIFACT_BYTES = 1_000_000               # 1 MB per captured artifact (bounded)
_MAX_OUTPUT_BYTES = 4_000_000                 # bounded stdout/stderr buffering


class CodexAdapterError(Exception):
    """A sanitized, secret-free adapter failure (→ factory WORKER_ERROR). Its message is a
    static code only — never a provider key, prompt, env value, or raw stderr."""


@dataclass(frozen=True)
class CodexProcessResult:
    exit_code: int
    stdout: str
    stderr: str


# A transport runs the process boundary. Real default below; tests inject a fake that also
# simulates Codex writing files into ``cwd``. It MUST raise WorkerTimeoutError on deadline.
Transport = Callable[[list, str, dict, str, int], CodexProcessResult]


def codex_bin() -> Optional[str]:
    explicit = os.environ.get(_BIN_ENV)
    if explicit:
        return explicit if os.path.exists(explicit) else None
    return shutil.which("codex")


def _real_transport(argv: list, stdin_text: str, env: dict, cwd: str, timeout: int) -> CodexProcessResult:
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=env, start_new_session=True,   # own group -> whole tree killable
        )
    except FileNotFoundError as exc:
        raise CodexAdapterError("CODEX_BINARY_MISSING") from exc
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.communicate()
        raise WorkerTimeoutError("CODEX_TIMEOUT") from exc
    return CodexProcessResult(proc.returncode, (out or "")[:_MAX_OUTPUT_BYTES], (err or "")[:_MAX_OUTPUT_BYTES])


class CodexExecWorker:
    """WorkerAdapter driving ``codex exec``. ``transport`` is the injectable process seam."""

    kind = WORKER_KIND

    def __init__(self, *, transport: Optional[Transport] = None):
        self._transport = transport or _real_transport

    # ---- environment (constructed minimally; never cloned then scrubbed) -------------
    def _child_env(self) -> dict:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        key = os.environ.get(_API_KEY_ENV)
        if not key:
            raise CodexAdapterError("CODEX_AUTH_MISSING")
        env["OPENAI_API_KEY"] = key   # credential enters ONLY the child env, never argv
        return env

    def _argv(self, bin_path: str, model: str, workspace: str) -> list:
        return [
            bin_path, "exec", "-", "--json",
            "--model", model,
            "--cd", workspace,
            "--sandbox", "workspace-write",
            "--ask-for-approval", "never",
            "--skip-git-repo-check",
            "--ephemeral",
            "-c", "shell_environment_policy.ignore_default_excludes=false",
        ]

    def execute(self, request: WorkerRequest) -> WorkerResult:
        payload = dict(request.task_payload or {})
        prompt = payload.get("prompt")
        model = payload.get("model")
        artifact_paths = list(payload.get("artifact_paths") or [])
        if not isinstance(prompt, str) or not prompt.strip():
            raise CodexAdapterError("CODEX_PROMPT_MISSING")
        if not isinstance(model, str) or not model.strip():
            raise CodexAdapterError("CODEX_MODEL_MISSING")   # model must be explicitly frozen
        if not artifact_paths:
            raise CodexAdapterError("CODEX_ARTIFACT_PATHS_MISSING")

        workspace = request.workspace_ref
        if not workspace or not os.path.isdir(workspace):
            raise CodexAdapterError("CODEX_WORKSPACE_INVALID")
        ws.assert_isolated_workspace(workspace)   # never the canonical AIDAN repo

        bin_path = codex_bin()
        if not bin_path:
            raise CodexAdapterError("CODEX_BINARY_MISSING")

        env = self._child_env()
        argv = self._argv(bin_path, model, workspace)
        result = self._transport(argv, prompt, env, workspace, int(request.timeout_seconds or 120))
        # WorkerTimeoutError from the transport propagates untouched -> factory records TIMEOUT.

        if result.exit_code != 0:
            raise CodexAdapterError("CODEX_NONZERO_EXIT")   # sanitized; no stderr/secret

        session_id, terminal_ok = self._parse_events(result.stdout)
        if not terminal_ok:
            raise CodexAdapterError("CODEX_NO_TERMINAL_EVENT")

        artifacts = self._capture_artifacts(workspace, artifact_paths)
        external_result_id = self._result_id(request, session_id, artifacts)
        structured = {"exit_code": 0, "terminal": "completed", "artifact_count": len(artifacts)}
        if session_id is not None:
            structured["provider_session_id"] = session_id   # only when actually present
        return WorkerResult(
            worker_kind=self.kind, worker_version=WORKER_VERSION,
            external_result_id=external_result_id,
            reported_outcome="codex-exec-completed",   # a CLAIM; verifier decides correctness
            structured_output=structured, artifacts=tuple(artifacts),
        )

    # ---- documented JSONL event parsing (bounded, safe) ------------------------------
    def _parse_events(self, stdout: str):
        session_id = None
        saw_terminal_success = False
        saw_terminal_failure = False
        any_event = False
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception as exc:
                raise CodexAdapterError("CODEX_MALFORMED_EVENT") from exc
            if not isinstance(ev, dict):
                raise CodexAdapterError("CODEX_MALFORMED_EVENT")
            any_event = True
            etype = ev.get("type")
            if etype in ("thread.started", "session.created") and session_id is None:
                sid = ev.get("thread_id") or ev.get("session_id") or ev.get("id")
                if isinstance(sid, str) and sid:
                    session_id = sid
            if etype in ("turn.completed", "response.completed"):
                saw_terminal_success = True
            if etype in ("turn.failed", "error", "response.failed"):
                saw_terminal_failure = True
        if not any_event:
            raise CodexAdapterError("CODEX_NO_EVENTS")
        if saw_terminal_failure:
            raise CodexAdapterError("CODEX_TURN_FAILED")
        return session_id, saw_terminal_success

    # ---- artifact capture (only frozen paths; strict safety) -------------------------
    def _capture_artifacts(self, workspace: str, artifact_paths: list) -> list:
        root = Path(workspace).resolve()
        out = []
        for rel in artifact_paths:
            if not isinstance(rel, str) or not rel:
                raise CodexAdapterError("CODEX_ARTIFACT_PATH_INVALID")
            norm = rel.replace("\\", "/")
            if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
                raise CodexAdapterError("CODEX_ARTIFACT_ABSOLUTE")
            if ".." in norm.split("/"):
                raise CodexAdapterError("CODEX_ARTIFACT_TRAVERSAL")
            target = (root / norm)
            real = Path(os.path.realpath(target))
            if real != root and root not in real.parents:
                raise CodexAdapterError("CODEX_ARTIFACT_SYMLINK_ESCAPE")
            if not target.exists() or not target.is_file() or target.is_symlink():
                raise CodexAdapterError("CODEX_ARTIFACT_NOT_A_FILE")
            data = target.read_bytes()
            if len(data) > _MAX_ARTIFACT_BYTES:
                raise CodexAdapterError("CODEX_ARTIFACT_TOO_LARGE")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CodexAdapterError("CODEX_ARTIFACT_NOT_UTF8") from exc
            out.append({"artifact_key": norm, "artifact_type": "FILE", "ref": norm, "content": content})
        return out

    def _result_id(self, request: WorkerRequest, session_id, artifacts) -> str:
        if session_id:
            return f"codex-session:{session_id}"   # real provider identity when present
        # Otherwise a deterministic, clearly kernel-derived id — never a fabricated provider id.
        h = hashlib.sha256()
        h.update(str(request.attempt_id).encode())
        for a in artifacts:
            h.update(a["artifact_key"].encode())
            h.update(a["content"].encode("utf-8"))
        return f"codex-local:{h.hexdigest()[:24]}"
