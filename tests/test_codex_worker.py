"""CodexExecWorker unit tests — deterministic, no real Codex, no DB, no bwrap.

Proves the corrected contract: CODEX_API_KEY auth, minimal + isolated child environment
(fresh HOME/TMPDIR/CODEX_HOME, no ambient user config), locked argv, artifact safety,
documented-only event parsing, fail-closed canonical-workspace guard, git-repo requirement,
and typed timeout — all via an injected fake process seam.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aidan_core.errors import BuildAuthorityError, ProviderExecutionFailure, WorkerTimeoutError
from aidan_core.factory import codex_worker as cw
from aidan_core.factory.codex_worker import CodexAdapterError, CodexExecWorker, CodexProcessResult
from aidan_core.factory.workers import WorkerRequest

GOOD_EVENTS = [{"type": "thread.started", "thread_id": "th_ABC123"}, {"type": "turn.completed"}]


class FakeCodex:
    def __init__(self, *, writes=None, exit_code=0, events=GOOD_EVENTS, raw_stdout=None, raise_exc=None):
        self.calls = []
        self.writes = writes or {}
        self.exit_code = exit_code
        self.events = events
        self.raw_stdout = raw_stdout
        self.raise_exc = raise_exc

    def __call__(self, argv, stdin_text, env, cwd, timeout):
        rec = {"argv": list(argv), "env": dict(env), "stdin": stdin_text, "cwd": cwd, "timeout": timeout}
        try:
            rec["home_listing"] = sorted(os.listdir(env["HOME"]))          # isolation evidence
            rec["codex_home_listing"] = sorted(os.listdir(env["CODEX_HOME"]))
        except Exception:
            rec["home_listing"] = None
        self.calls.append(rec)
        if self.raise_exc:
            raise self.raise_exc
        for rel, content in self.writes.items():
            p = Path(cwd) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        stdout = self.raw_stdout if self.raw_stdout is not None else "\n".join(json.dumps(e) for e in self.events)
        return CodexProcessResult(self.exit_code, stdout, "sanitized-stderr")


def _gitws(p) -> str:
    (Path(p) / ".git").mkdir(exist_ok=True)   # isolated venture git repo
    return str(p)


def _req(workspace, **over):
    payload = {"prompt": "implement add(a,b)", "model": "gpt-5-mini", "artifact_paths": ["candidate.py"]}
    payload.update(over)
    return WorkerRequest(
        action_request_id="a1", attempt_id="att1", venture_id="v1", spec_hash="s1",
        worker_kind="codex-exec", task_payload=payload, declared_inputs={}, capabilities=(),
        timeout_seconds=30, workspace_ref=str(workspace), expected_output_contract={})


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("WORKER_CODEX_API_KEY", "codex-SECRETKEY-should-never-leak")
    monkeypatch.setattr(cw, "codex_bin", lambda: "/usr/bin/codex")


def _run(fake, workspace, **over):
    return CodexExecWorker(transport=fake).execute(_req(_gitws(workspace), **over))


# ---- auth + secret / environment boundary -----------------------------------

def test_child_env_uses_codex_api_key_and_is_minimal(tmp_path, monkeypatch):
    for k in ("OPENAI_API_KEY", "OTHER_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
              "PASSWORD", "CREDENTIAL", "WORKER_SECRET", "RANDOM_KEY", "X_SECRET", "Y_TOKEN"):
        monkeypatch.setenv(k, "hostile-" + k)
    fake = FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n"})
    _run(fake, tmp_path)
    env = fake.calls[0]["env"]
    assert env["CODEX_API_KEY"] == "codex-SECRETKEY-should-never-leak"
    assert set(env) == {"PATH", "HOME", "TMPDIR", "CODEX_HOME", "LANG", "CODEX_API_KEY"}
    for k in ("OPENAI_API_KEY", "OTHER_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "PASSWORD",
              "CREDENTIAL", "WORKER_SECRET", "RANDOM_KEY", "X_SECRET", "Y_TOKEN", "WORKER_CODEX_API_KEY"):
        assert k not in env


def test_home_tmp_and_codex_home_are_fresh_and_isolated(tmp_path, monkeypatch):
    # Hostile ambient HOME with a Codex config/auth — the child must NOT be pointed at it.
    host_home = tmp_path / "hosthome"
    (host_home / ".codex").mkdir(parents=True)
    (host_home / ".codex" / "auth.json").write_text('{"token":"leak"}')
    (host_home / ".codex" / "config.toml").write_text("x=1")
    monkeypatch.setenv("HOME", str(host_home))
    fake = FakeCodex(writes={"candidate.py": "x=1\n"})
    _run(fake, tmp_path)
    env = fake.calls[0]["env"]
    assert env["HOME"] != str(host_home)
    assert env["CODEX_HOME"] != str(host_home / ".codex")
    assert env["HOME"] != env["TMPDIR"] != env["CODEX_HOME"]
    assert fake.calls[0]["home_listing"] == []          # fresh empty home (no .codex)
    assert fake.calls[0]["codex_home_listing"] == []     # fresh empty CODEX_HOME


def test_prompt_via_stdin_and_locked_flags(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n"})
    _run(fake, tmp_path)
    call = fake.calls[0]
    argv = call["argv"]
    assert call["stdin"] == "implement add(a,b)"
    assert "implement add(a,b)" not in " ".join(argv)
    assert argv[1:4] == ["exec", "-", "--json"]
    for tok in ("--model", "gpt-5-mini", "--sandbox", "workspace-write", "--ask-for-approval",
                "never", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "shell_environment_policy.ignore_default_excludes=false"):
        assert tok in argv
    assert "--skip-git-repo-check" not in argv           # removed: git-repo safety kept


def test_credential_never_appears_in_argv(tmp_path):
    _run(FakeCodex(writes={"candidate.py": "x=1\n"}), tmp_path)


def test_missing_credential_fails_sanitized(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKER_CODEX_API_KEY", raising=False)
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(), tmp_path)
    assert str(ei.value) == "CODEX_AUTH_MISSING"


def test_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "codex_bin", lambda: None)
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(), tmp_path)
    assert str(ei.value) == "CODEX_BINARY_MISSING"


# ---- workspace: fail-closed canonical guard + git-repo requirement -----------

def test_missing_workspace_dir_rejected(tmp_path):
    with pytest.raises(CodexAdapterError) as ei:
        CodexExecWorker(transport=FakeCodex()).execute(_req(tmp_path / "nope"))
    assert str(ei.value) == "CODEX_WORKSPACE_INVALID"


def test_canonical_repo_rejected(tmp_path, monkeypatch):
    canonical = tmp_path / "os-repo"
    (canonical / ".git").mkdir(parents=True)
    monkeypatch.setenv("AIDAN_OS_REPO_ROOT", str(canonical))
    with pytest.raises(BuildAuthorityError):
        CodexExecWorker(transport=FakeCodex()).execute(_req(canonical))


def test_workspace_guard_fails_closed_without_canonical_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(cw.ws, "canonical_os_repo_root", lambda: None)   # installed-wheel-like
    with pytest.raises(BuildAuthorityError):
        CodexExecWorker(transport=FakeCodex()).execute(_req(_gitws(tmp_path)))


def test_non_git_workspace_rejected(tmp_path):
    with pytest.raises(CodexAdapterError) as ei:
        CodexExecWorker(transport=FakeCodex()).execute(_req(str(tmp_path)))   # no .git
    assert str(ei.value) == "CODEX_WORKSPACE_NOT_GIT"


# ---- artifact safety --------------------------------------------------------

# Path FORMAT errors are frozen-input errors caught PRE-invocation (safe -> WORKER_ERROR).
@pytest.mark.parametrize("paths,code", [
    (["/etc/passwd"], "CODEX_ARTIFACT_ABSOLUTE"),
    (["../escape.py"], "CODEX_ARTIFACT_TRAVERSAL"),
])
def test_artifact_path_format_rejected_pre_invocation(tmp_path, paths, code):
    fake = FakeCodex()
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path, artifact_paths=paths)
    assert str(ei.value) == code
    assert fake.calls == []                       # provider never invoked


# Output-dependent artifact failures are POST-invocation -> known WORKER_ERROR + cost-bearing.
@pytest.mark.parametrize("fake,paths", [
    (FakeCodex(), ["missing.py"]),                                  # provider produced no file
    (FakeCodex(writes={"outdir/x": "y"}), ["outdir"]),             # a directory
    (FakeCodex(writes={"candidate.py": b"\xff\xfe\x00bin"}), ["candidate.py"]),   # non-utf8
    (FakeCodex(writes={"candidate.py": "#" * (cw._MAX_ARTIFACT_BYTES + 1)}), ["candidate.py"]),  # oversized
])
def test_output_dependent_artifact_failures_are_cost_bearing_worker_error(tmp_path, fake, paths):
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(fake, tmp_path, artifact_paths=paths)
    assert ei.value.failure_class == "WORKER_ERROR" and ei.value.model == "gpt-5-mini"
    assert fake.calls != []                       # provider WAS invoked (cost may exist)


def test_undeclared_files_are_ignored(tmp_path):
    res = _run(FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n", "sneaky.py": "evil\n"}), tmp_path)
    assert {a["artifact_key"] for a in res.artifacts} == {"candidate.py"}


# ---- documented-only event parsing ------------------------------------------

def test_success_captures_candidate_and_provider_thread(tmp_path):
    res = _run(FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n"}), tmp_path)
    assert res.reported_outcome == "codex-exec-completed"
    assert res.external_result_id == "codex-thread:th_ABC123"
    assert res.structured_output["provider_thread_id"] == "th_ABC123"


def test_absent_provider_thread_uses_kernel_derived_id(tmp_path):
    res = _run(FakeCodex(writes={"candidate.py": "x=1\n"}, events=[{"type": "turn.completed"}]), tmp_path)
    assert res.external_result_id.startswith("codex-local:")
    assert "provider_thread_id" not in res.structured_output


def test_bounded_token_usage_captured_when_present(tmp_path):
    events = [{"type": "thread.started", "thread_id": "th"},
              {"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 340, "cost": 99}}]
    res = _run(FakeCodex(writes={"candidate.py": "x=1\n"}, events=events), tmp_path)
    assert res.structured_output["token_usage"] == {"input_tokens": 1200, "output_tokens": 340}  # only bounded ints


def test_token_usage_absent_when_not_reported(tmp_path):
    res = _run(FakeCodex(writes={"candidate.py": "x=1\n"}), tmp_path)   # GOOD_EVENTS carry no usage
    assert "token_usage" not in res.structured_output


def test_undocumented_event_aliases_are_not_terminal(tmp_path):
    # session.created / response.completed are NOT documented codex-exec events -> no terminal.
    # POST-invocation known failure (WORKER_ERROR + cost-bearing), NOT RECOVERY_REQUIRED.
    fake = FakeCodex(writes={"candidate.py": "x=1\n"},
                     events=[{"type": "session.created", "id": "s"}, {"type": "response.completed"}])
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(fake, tmp_path)
    assert ei.value.failure_class == "WORKER_ERROR"


# Every POST-invocation failure is a KNOWN-outcome WORKER_ERROR that bears possible cost — never a
# zero-cost release and never RECOVERY_REQUIRED for mere cost uncertainty.
@pytest.mark.parametrize("fake", [
    FakeCodex(exit_code=1),
    FakeCodex(raw_stdout="not json at all"),
    FakeCodex(raw_stdout=""),
    FakeCodex(events=[{"type": "thread.started", "thread_id": "t"}]),
    FakeCodex(events=[{"type": "turn.failed"}]),
    FakeCodex(events=[{"type": "error"}]),
])
def test_post_invocation_failures_are_cost_bearing_worker_error(tmp_path, fake):
    fake.writes = {"candidate.py": "x=1\n"}
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(fake, tmp_path)
    assert ei.value.failure_class == "WORKER_ERROR"
    assert fake.calls != []


def test_known_failure_preserves_trusted_usage_for_cost(tmp_path):
    # turn.failed carrying usage -> the trusted usage survives to the factory for cost derivation.
    fake = FakeCodex(writes={"candidate.py": "x=1\n"},
                     events=[{"type": "turn.failed", "usage": {"input_tokens": 900, "output_tokens": 100}}])
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(fake, tmp_path)
    assert ei.value.failure_class == "WORKER_ERROR"
    assert ei.value.usage == {"input_tokens": 900, "output_tokens": 100} and ei.value.model == "gpt-5-mini"
    assert ei.value.code == "CODEX_TURN_FAILED"


# ---- HONEST provider-contact evidence + bounded diagnostic fields -----------
# Crossing the subprocess boundary is NOT proof an OpenAI API request occurred. provider_contact
# is OBSERVED only when a documented thread.started was actually parsed; else UNKNOWN.

def test_nonzero_exit_without_thread_is_contact_unknown(tmp_path):
    # The smoke-#2 shape: process exits non-zero with no terminal/thread event.
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(FakeCodex(writes={"candidate.py": "x=1\n"}, exit_code=4, events=[]), tmp_path)
    assert ei.value.code == "CODEX_NONZERO_EXIT" and ei.value.process_exit_code == 4
    assert ei.value.provider_contact == "UNKNOWN"      # boundary crossed, but no thread.started seen


def test_nonzero_exit_after_thread_started_is_contact_observed(tmp_path):
    # A thread WAS opened, then the process failed -> contact is genuinely OBSERVED.
    fake = FakeCodex(writes={"candidate.py": "x=1\n"}, exit_code=7,
                     events=[{"type": "thread.started", "thread_id": "th"}])
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(fake, tmp_path)
    assert ei.value.code == "CODEX_NONZERO_EXIT" and ei.value.process_exit_code == 7
    assert ei.value.provider_contact == "OBSERVED"


# ---- timeout ----------------------------------------------------------------

def test_post_invocation_timeout_is_cost_bearing_timeout(tmp_path):
    # A timeout AFTER invocation keeps its TRUE TIMEOUT class and is cost-bearing (no usage) —
    # never a zero-cost release, never RECOVERY_REQUIRED.
    with pytest.raises(ProviderExecutionFailure) as ei:
        _run(FakeCodex(raise_exc=WorkerTimeoutError("CODEX_TIMEOUT")), tmp_path)
    assert ei.value.failure_class == "TIMEOUT" and ei.value.usage is None and ei.value.model == "gpt-5-mini"
    assert ei.value.code == "CODEX_TIMEOUT_AFTER_INVOCATION"
    assert ei.value.provider_contact == "UNKNOWN" and ei.value.process_exit_code is None


# ---- behavioural candidates (unit-level: captured bytes verbatim) -----------

@pytest.mark.parametrize("src", [
    "def add(a, b):\n    return a + b\n",
    "def add(a, b):\n    return b + a  # different bytes\n",
    "def add(a, b):\n    return a * b  # wrong\n",
])
def test_candidate_bytes_captured_verbatim(tmp_path, src):
    res = _run(FakeCodex(writes={"candidate.py": src}), tmp_path)
    assert res.artifacts[0]["content"] == src
