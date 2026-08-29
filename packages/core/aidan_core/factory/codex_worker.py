"""Provider-neutral coding worker adapter for the OpenAI Codex CLI (``codex exec``).

A replaceable :class:`~aidan_core.factory.workers.WorkerAdapter`. It receives a bounded
``WorkerRequest`` (no DB, no canonical authority), drives ``codex exec`` non-interactively
in an isolated venture git workspace, and returns a ``WorkerResult`` — a CLAIM plus captured
candidate artifacts. Whether the produced code is CORRECT is decided later and only by the
deterministic ``TestExecutionVerifier`` (behavioural Bubblewrap execution) — never by this
adapter, the worker's self-report, or the provider's own success signal.

Locked contract (current official OpenAI Codex automation docs, 2026-08):
  argv:  codex exec - --json --model <FROZEN> --cd <WORKSPACE> --sandbox workspace-write
             --ask-for-approval never --ephemeral --ignore-user-config --ignore-rules
             -c shell_environment_policy.ignore_default_excludes=false
  prompt: delivered on STDIN (the ``-`` positional), never on argv;
  auth:   CODEX_API_KEY — the invocation-scoped Codex automation credential — provided ONLY
          in the child process environment (never argv/log/result). Official docs warn NOT to
          set it job-level; the adapter sets it only for this one invocation.
  isolation: a FRESH empty HOME, TMPDIR and CODEX_HOME are created per invocation, so Codex
          never reads the host user's ``~/.codex/config.toml``/``auth.json`` or state;
          ``--ignore-user-config`` and ``--ignore-rules`` additionally refuse ambient user
          config and execpolicy ``.rules``. The child environment is CONSTRUCTED minimally
          (never ``os.environ.copy()``), so no unrelated host secret can leak.

Security note: the load-bearing secret boundary is the MINIMAL constructed parent environment
(only CODEX_API_KEY plus non-secret runtime values). ``ignore_default_excludes=false`` is a
best-effort defence-in-depth that makes Codex withhold KEY/SECRET/TOKEN vars from tools it
spawns; a stronger ``shell_environment_policy.inherit`` contract was NOT adopted because its
exact accepted values could not be confirmed from current docs. The adapter owns the
subprocess timeout, kills the whole process tree, and raises ``WorkerTimeoutError`` (→ the
factory records a canonical ``TIMEOUT``); it performs zero retries. No real Codex process is
required to test this module — the process boundary is an injectable seam.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..build import workspace as ws
from ..errors import AmbiguousExternalEffectError, WorkerTimeoutError
from .workers import WorkerRequest, WorkerResult

WORKER_KIND = "codex-exec"
WORKER_VERSION = "2"

# AIDAN-side host configuration. The Codex invocation credential is CODEX_API_KEY (not
# OPENAI_API_KEY) per current official automation docs.
_API_KEY_ENV = "WORKER_CODEX_API_KEY"         # host provides the key here; passed to child as CODEX_API_KEY
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

    # ---- environment: minimal + fully isolated Codex home (never ambient) -------------
    def _child_env(self, *, home: str, tmpdir: str, codex_home: str) -> dict:
        key = os.environ.get(_API_KEY_ENV)
        if not key:
            raise CodexAdapterError("CODEX_AUTH_MISSING")
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": home,              # fresh empty per-invocation home (no host ~/.codex)
            "TMPDIR": tmpdir,          # fresh empty per-invocation tmp
            "CODEX_HOME": codex_home,  # fresh empty Codex state/config/auth dir
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "CODEX_API_KEY": key,      # invocation-scoped credential; child env only, never argv
        }

    def _argv(self, bin_path: str, model: str, workspace: str) -> list:
        return [
            bin_path, "exec", "-", "--json",
            "--model", model,
            "--cd", workspace,
            "--sandbox", "workspace-write",
            "--ask-for-approval", "never",
            "--ephemeral",
            "--ignore-user-config",     # do not load $CODEX_HOME/config.toml semantics from ambient config
            "--ignore-rules",           # skip user/project execpolicy .rules
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
        self._assert_artifact_path_formats(artifact_paths)   # frozen-input format check (pre-invocation)

        workspace = request.workspace_ref
        if not workspace or not os.path.isdir(workspace):
            raise CodexAdapterError("CODEX_WORKSPACE_INVALID")
        # Fail CLOSED on canonical-repository identity: the OS repo (or a nested path) is never a
        # workspace, and if trusted canonical identity is unavailable the boundary refuses to run.
        ws.assert_isolated_workspace(workspace, require_canonical=True)
        if not os.path.isdir(os.path.join(workspace, ".git")):
            raise CodexAdapterError("CODEX_WORKSPACE_NOT_GIT")   # isolated venture git repo required

        bin_path = codex_bin()
        if not bin_path:
            raise CodexAdapterError("CODEX_BINARY_MISSING")

        # Everything ABOVE this point is a pre-invocation guard: the provider was never
        # invoked, so a failure here is a plain WORKER_ERROR and the reservation may be safely
        # released (no billable effect). Everything from the transport call onward is
        # POST-invocation: the paid provider may already have consumed tokens, so ANY failure
        # (timeout, non-zero exit, malformed/absent terminal, bad artifact) is treated as an
        # ambiguous external effect -> the factory records RECOVERY_REQUIRED and HOLDS the
        # reservation (UNKNOWN COST != ZERO COST); it is never released as if cost were zero.
        with tempfile.TemporaryDirectory(prefix="codex-iso-") as base:
            home = os.path.join(base, "home")
            tmpdir = os.path.join(base, "tmp")
            codex_home = os.path.join(base, "codex_home")
            for d in (home, tmpdir, codex_home):
                os.mkdir(d)
            env = self._child_env(home=home, tmpdir=tmpdir, codex_home=codex_home)
            argv = self._argv(bin_path, model, workspace)
            try:
                result = self._transport(argv, prompt, env, workspace, int(request.timeout_seconds or 120))
            except WorkerTimeoutError as exc:
                # Timed out AFTER invocation: tokens may already be spent -> conservative.
                raise AmbiguousExternalEffectError("CODEX_TIMEOUT_AFTER_INVOCATION") from exc

            try:
                if result.exit_code != 0:
                    raise CodexAdapterError("CODEX_NONZERO_EXIT")   # sanitized; no stderr/secret
                session_id, terminal_ok, usage = self._parse_events(result.stdout)
                if not terminal_ok:
                    raise CodexAdapterError("CODEX_NO_TERMINAL_EVENT")
                artifacts = self._capture_artifacts(workspace, artifact_paths)
            except CodexAdapterError as exc:
                # The provider ran; its cost is unknown -> do NOT let capital be released as zero.
                raise AmbiguousExternalEffectError(str(exc)) from exc

        external_result_id = self._result_id(request, session_id, artifacts)
        structured = {"exit_code": 0, "terminal": "completed", "artifact_count": len(artifacts)}
        if session_id is not None:
            structured["provider_thread_id"] = session_id   # only when actually present
        if usage is not None:
            # Bounded provider-reported token usage — the ONLY input the kernel's trusted,
            # frozen-pricing cost estimator consumes (a self-reported dollar cost is never used).
            structured["token_usage"] = usage
        return WorkerResult(
            worker_kind=self.kind, worker_version=WORKER_VERSION,
            external_result_id=external_result_id,
            reported_outcome="codex-exec-completed",   # a CLAIM; verifier decides correctness
            structured_output=structured, artifacts=tuple(artifacts),
        )

    # ---- documented JSONL event parsing (only documented Codex event types) -----------
    def _parse_events(self, stdout: str):
        session_id = None
        usage = None
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
            # Documented Codex exec event stream: thread.started, turn.started, turn.completed,
            # turn.failed, item.*, error.
            if etype == "thread.started" and session_id is None:
                sid = ev.get("thread_id")
                if isinstance(sid, str) and sid:
                    session_id = sid
            elif etype == "turn.completed":
                saw_terminal_success = True
                usage = self._extract_usage(ev.get("usage"))
            elif etype in ("turn.failed", "error"):
                saw_terminal_failure = True
        if not any_event:
            raise CodexAdapterError("CODEX_NO_EVENTS")
        if saw_terminal_failure:
            raise CodexAdapterError("CODEX_TURN_FAILED")
        return session_id, saw_terminal_success, usage

    @staticmethod
    def _extract_usage(raw):
        """Bounded, safe token usage: only non-negative int input/output token counts."""
        if not isinstance(raw, dict):
            return None
        inp = raw.get("input_tokens")
        out = raw.get("output_tokens")
        if isinstance(inp, int) and isinstance(out, int) and inp >= 0 and out >= 0:
            return {"input_tokens": inp, "output_tokens": out}
        return None

    # ---- artifact path FORMAT check (pre-invocation; frozen inputs, no provider effect) ---
    @staticmethod
    def _assert_artifact_path_formats(artifact_paths: list) -> None:
        for rel in artifact_paths:
            if not isinstance(rel, str) or not rel:
                raise CodexAdapterError("CODEX_ARTIFACT_PATH_INVALID")
            norm = rel.replace("\\", "/")
            if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
                raise CodexAdapterError("CODEX_ARTIFACT_ABSOLUTE")
            if ".." in norm.split("/"):
                raise CodexAdapterError("CODEX_ARTIFACT_TRAVERSAL")

    # ---- artifact capture (post-invocation; depends on provider output) ---------------
    def _capture_artifacts(self, workspace: str, artifact_paths: list) -> list:
        root = Path(workspace).resolve()
        out = []
        for rel in artifact_paths:
            norm = rel.replace("\\", "/")
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
            return f"codex-thread:{session_id}"   # real provider identity when present
        # Otherwise a deterministic, clearly kernel-derived id — never a fabricated provider id.
        h = hashlib.sha256()
        h.update(str(request.attempt_id).encode())
        for a in artifacts:
            h.update(a["artifact_key"].encode())
            h.update(a["content"].encode("utf-8"))
        return f"codex-local:{h.hexdigest()[:24]}"
