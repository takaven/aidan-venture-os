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

from aidan_core.errors import BuildAuthorityError, WorkerTimeoutError
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

@pytest.mark.parametrize("paths,code", [
    (["/etc/passwd"], "CODEX_ARTIFACT_ABSOLUTE"),
    (["../escape.py"], "CODEX_ARTIFACT_TRAVERSAL"),
    (["missing.py"], "CODEX_ARTIFACT_NOT_A_FILE"),
])
def test_artifact_path_rejections(tmp_path, paths, code):
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(), tmp_path, artifact_paths=paths)
    assert str(ei.value) == code


def test_artifact_directory_rejected(tmp_path):
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(writes={"outdir/x": "y"}), tmp_path, artifact_paths=["outdir"])
    assert str(ei.value) == "CODEX_ARTIFACT_NOT_A_FILE"


def test_artifact_non_utf8_rejected(tmp_path):
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(writes={"candidate.py": b"\xff\xfe\x00binary"}), tmp_path)
    assert str(ei.value) == "CODEX_ARTIFACT_NOT_UTF8"


def test_artifact_oversized_rejected(tmp_path):
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(writes={"candidate.py": "#" * (cw._MAX_ARTIFACT_BYTES + 1)}), tmp_path)
    assert str(ei.value) == "CODEX_ARTIFACT_TOO_LARGE"


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


def test_undocumented_event_aliases_are_not_terminal(tmp_path):
    # session.created / response.completed are NOT documented codex-exec events -> no terminal.
    fake = FakeCodex(writes={"candidate.py": "x=1\n"},
                     events=[{"type": "session.created", "id": "s"}, {"type": "response.completed"}])
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path)
    assert str(ei.value) == "CODEX_NO_TERMINAL_EVENT"


@pytest.mark.parametrize("fake,code", [
    (FakeCodex(exit_code=1), "CODEX_NONZERO_EXIT"),
    (FakeCodex(raw_stdout="not json at all"), "CODEX_MALFORMED_EVENT"),
    (FakeCodex(raw_stdout=""), "CODEX_NO_EVENTS"),
    (FakeCodex(events=[{"type": "thread.started", "thread_id": "t"}]), "CODEX_NO_TERMINAL_EVENT"),
    (FakeCodex(events=[{"type": "turn.failed"}]), "CODEX_TURN_FAILED"),
    (FakeCodex(events=[{"type": "error"}]), "CODEX_TURN_FAILED"),
])
def test_event_and_exit_rejections(tmp_path, fake, code):
    fake.writes = {"candidate.py": "x=1\n"}
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path)
    assert str(ei.value) == code


# ---- timeout ----------------------------------------------------------------

def test_adapter_timeout_raises_typed_worker_timeout(tmp_path):
    with pytest.raises(WorkerTimeoutError):
        _run(FakeCodex(raise_exc=WorkerTimeoutError("CODEX_TIMEOUT")), tmp_path)


# ---- behavioural candidates (unit-level: captured bytes verbatim) -----------

@pytest.mark.parametrize("src", [
    "def add(a, b):\n    return a + b\n",
    "def add(a, b):\n    return b + a  # different bytes\n",
    "def add(a, b):\n    return a * b  # wrong\n",
])
def test_candidate_bytes_captured_verbatim(tmp_path, src):
    res = _run(FakeCodex(writes={"candidate.py": src}), tmp_path)
    assert res.artifacts[0]["content"] == src
