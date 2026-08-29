"""CodexExecWorker unit tests — deterministic, no real Codex, no DB, no bwrap.

Proves the secret/environment boundary, argv/stdin contract, artifact safety, documented
event parsing, and typed timeout — all via an injected fake process seam.
"""
from __future__ import annotations

import json
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
        self.calls.append({"argv": list(argv), "env": dict(env), "stdin": stdin_text,
                           "cwd": cwd, "timeout": timeout})
        if self.raise_exc:
            raise self.raise_exc
        for rel, content in self.writes.items():
            p = Path(cwd) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            # Always write bytes (no platform newline translation) so captured content is exact.
            p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        stdout = self.raw_stdout if self.raw_stdout is not None else "\n".join(json.dumps(e) for e in self.events)
        return CodexProcessResult(self.exit_code, stdout, "sanitized-stderr")


def _req(workspace, **over):
    payload = {"prompt": "implement add(a,b)", "model": "gpt-5-mini", "artifact_paths": ["candidate.py"]}
    payload.update(over)
    return WorkerRequest(
        action_request_id="a1", attempt_id="att1", venture_id="v1", spec_hash="s1",
        worker_kind="codex-exec", task_payload=payload, declared_inputs={}, capabilities=(),
        timeout_seconds=30, workspace_ref=str(workspace), expected_output_contract={})


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("WORKER_OPENAI_API_KEY", "sk-SECRETKEYVALUE-should-never-leak")
    monkeypatch.setattr(cw, "codex_bin", lambda: "/usr/bin/codex")


def _run(fake, workspace, **over):
    return CodexExecWorker(transport=fake).execute(_req(workspace, **over))


# ---- secret / environment boundary ------------------------------------------

def test_child_env_is_minimal_and_excludes_host_secrets(tmp_path, monkeypatch):
    for k in ("OTHER_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "PASSWORD",
              "CREDENTIAL", "WORKER_SECRET", "RANDOM_KEY", "X_SECRET", "Y_TOKEN"):
        monkeypatch.setenv(k, "hostile-" + k)
    fake = FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n"})
    _run(fake, tmp_path)
    env = fake.calls[0]["env"]
    assert env["OPENAI_API_KEY"] == "sk-SECRETKEYVALUE-should-never-leak"
    assert set(env) <= {"PATH", "HOME", "TMPDIR", "LANG", "OPENAI_API_KEY"}
    for k in ("OTHER_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "PASSWORD",
              "CREDENTIAL", "WORKER_SECRET", "RANDOM_KEY", "X_SECRET", "Y_TOKEN", "WORKER_OPENAI_API_KEY"):
        assert k not in env


def test_prompt_via_stdin_not_argv_and_locked_flags(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n"})
    _run(fake, tmp_path)
    call = fake.calls[0]
    argv = call["argv"]
    assert call["stdin"] == "implement add(a,b)"
    assert "implement add(a,b)" not in " ".join(argv)          # prompt never on argv
    assert argv[1:4] == ["exec", "-", "--json"]
    for tok in ("--model", "gpt-5-mini", "--sandbox", "workspace-write", "--ask-for-approval",
                "never", "--skip-git-repo-check", "--ephemeral",
                "shell_environment_policy.ignore_default_excludes=false"):
        assert tok in argv


def test_credential_never_appears_in_argv(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "x=1\n"})
    _run(fake, tmp_path)
    assert "sk-SECRETKEYVALUE-should-never-leak" not in " ".join(fake.calls[0]["argv"])


def test_missing_credential_fails_sanitized(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKER_OPENAI_API_KEY", raising=False)
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(), tmp_path)
    assert str(ei.value) == "CODEX_AUTH_MISSING" and "sk-" not in str(ei.value)


def test_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "codex_bin", lambda: None)
    with pytest.raises(CodexAdapterError) as ei:
        _run(FakeCodex(), tmp_path)
    assert str(ei.value) == "CODEX_BINARY_MISSING"


# ---- workspace / artifact safety --------------------------------------------

def test_workspace_must_be_isolated_directory(tmp_path, monkeypatch):
    with pytest.raises(CodexAdapterError):
        CodexExecWorker(transport=FakeCodex()).execute(_req(tmp_path / "nope"))
    canonical = tmp_path / "os-repo"
    canonical.mkdir()
    monkeypatch.setenv("AIDAN_OS_REPO_ROOT", str(canonical))
    with pytest.raises(BuildAuthorityError):
        CodexExecWorker(transport=FakeCodex()).execute(_req(canonical))


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
    fake = FakeCodex(writes={"outdir/x": "y"})   # creates a dir 'outdir'
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path, artifact_paths=["outdir"])
    assert str(ei.value) == "CODEX_ARTIFACT_NOT_A_FILE"


def test_artifact_non_utf8_rejected(tmp_path):
    fake = FakeCodex(writes={"candidate.py": b"\xff\xfe\x00binary"})
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path)
    assert str(ei.value) == "CODEX_ARTIFACT_NOT_UTF8"


def test_artifact_oversized_rejected(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "#" * (cw._MAX_ARTIFACT_BYTES + 1)})
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path)
    assert str(ei.value) == "CODEX_ARTIFACT_TOO_LARGE"


def test_undeclared_files_are_ignored(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n", "sneaky.py": "evil\n"})
    res = _run(fake, tmp_path)
    keys = {a["artifact_key"] for a in res.artifacts}
    assert keys == {"candidate.py"}


# ---- documented event parsing -----------------------------------------------

def test_success_captures_candidate_and_provider_session(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "def add(a,b): return a+b\n"})
    res = _run(fake, tmp_path)
    assert res.reported_outcome == "codex-exec-completed"
    assert res.external_result_id == "codex-session:th_ABC123"
    assert res.structured_output["provider_session_id"] == "th_ABC123"
    assert res.artifacts[0]["content"] == "def add(a,b): return a+b\n"


def test_absent_provider_session_uses_kernel_derived_id(tmp_path):
    fake = FakeCodex(writes={"candidate.py": "x=1\n"}, events=[{"type": "turn.completed"}])
    res = _run(fake, tmp_path)
    assert res.external_result_id.startswith("codex-local:")
    assert "provider_session_id" not in res.structured_output


@pytest.mark.parametrize("fake,code", [
    (FakeCodex(exit_code=1), "CODEX_NONZERO_EXIT"),
    (FakeCodex(raw_stdout="not json at all"), "CODEX_MALFORMED_EVENT"),
    (FakeCodex(raw_stdout=""), "CODEX_NO_EVENTS"),
    (FakeCodex(events=[{"type": "thread.started", "thread_id": "t"}]), "CODEX_NO_TERMINAL_EVENT"),
    (FakeCodex(events=[{"type": "turn.failed"}]), "CODEX_TURN_FAILED"),
])
def test_event_and_exit_rejections(tmp_path, fake, code):
    fake.writes = {"candidate.py": "x=1\n"}
    with pytest.raises(CodexAdapterError) as ei:
        _run(fake, tmp_path)
    assert str(ei.value) == code


# ---- timeout ----------------------------------------------------------------

def test_adapter_timeout_raises_typed_worker_timeout(tmp_path):
    fake = FakeCodex(raise_exc=WorkerTimeoutError("CODEX_TIMEOUT"))
    with pytest.raises(WorkerTimeoutError):
        _run(fake, tmp_path)


# ---- behavioural candidates (unit-level: captured bytes) --------------------

@pytest.mark.parametrize("src", [
    "def add(a, b):\n    return a + b\n",
    "def add(a, b):\n    return b + a  # different bytes\n",
    "def add(a, b):\n    return a * b  # wrong\n",
])
def test_candidate_bytes_captured_verbatim(tmp_path, src):
    res = _run(FakeCodex(writes={"candidate.py": src}), tmp_path)
    assert res.artifacts[0]["content"] == src
