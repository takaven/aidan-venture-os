"""Trusted bounded sandbox test runner — Gate 8 deterministic coding verification.

Executes a FROZEN, kernel-owned test harness against candidate code inside a narrow
Bubblewrap (``bwrap``) sandbox and returns MACHINE-OWNED evidence. This is the trusted
evidence-collection stage that sits between candidate capture and the pure
``TestExecutionVerifier``:

    worker claim -> kernel captures candidate -> [THIS trusted runner executes the
    frozen test in a sandbox] -> machine-owned evidence -> pure verifier -> Proof Receipt

Load-bearing boundaries:
- the worker supplies NO part of the sandbox profile, the harness, or the command;
- the candidate runs with NO network, NO host secrets, NO host home, and NO writable
  access to anything except an isolated tmpfs scratch — in particular it can never
  reach the canonical AIDAN repository (it is simply not mounted);
- the runner never trusts candidate/worker self-report: the harness result is
  machine-parsed and the terminal/exit/timeout state is owned by this runner.

Scope (deliberately narrow, first proof): Linux + ``bwrap`` only; a single Python
stdlib-only candidate module + a frozen stdlib harness; no package install; no network.
On non-Linux / no-bwrap hosts :func:`bwrap_available` is False and callers must skip.

Supported-host contract (NOT generic Linux portability): a Linux host with bubblewrap
installed AND unprivileged user namespaces usable by bwrap — i.e. one of
``kernel.apparmor_restrict_unprivileged_userns=0`` (Ubuntu 23.10+ defaults it to 1,
which blocks bwrap's netns setup), an AppArmor profile permitting bwrap, or a setuid
bwrap. Where the sandbox cannot spawn, the runner returns ``SPAWN_FAILED`` and the
verifier fails closed (never a false success).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Identity of this trusted runner (bound into evidence; bump on any behaviour change).
RUNNER_KIND = "bwrap-python-stdlib"
RUNNER_VERSION = "1"

# Terminal states owned by the runner (never by the candidate).
COMPLETED = "COMPLETED"
TIMEOUT = "TIMEOUT"
SPAWN_FAILED = "SPAWN_FAILED"


def bwrap_path() -> Optional[str]:
    return shutil.which("bwrap")


def bwrap_available() -> bool:
    return bwrap_path() is not None


def bwrap_version() -> Optional[str]:
    exe = bwrap_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip() or None
    except Exception:
        return None


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonical_candidate_hash(candidate_files: dict) -> str:
    """Order-independent kernel hash over the candidate's (path, content) set."""
    payload = json.dumps({k: candidate_files[k] for k in sorted(candidate_files)},
                         sort_keys=True, ensure_ascii=True)
    return _sha256_text(payload)


@dataclass(frozen=True)
class SandboxEvidence:
    """Machine-owned result of a sandboxed test run. Not a worker claim."""

    runner_kind: str
    runner_version: str
    bwrap_version: Optional[str]
    terminal_state: str            # COMPLETED | TIMEOUT | SPAWN_FAILED
    exit_code: Optional[int]
    harness_result: str            # PASS | FAIL | NONE
    tests_total: Optional[int]
    tests_passed: Optional[int]
    candidate_sha256: str
    test_sha256: str
    timeout_seconds: int
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _bwrap_argv(exe: str, candidate_dir: str, test_dir: str) -> list:
    """The narrow sandbox profile. Only flags proven against the installed bwrap.

    - ``--unshare-all`` unshares user/pid/ipc/uts/cgroup/mount AND network (no egress);
    - ``--clearenv`` drops every inherited host secret; PATH/HOME are set explicitly;
    - only the read-only system dirs needed to run python, the read-only candidate and
      frozen test, an isolated tmpfs ``/work`` scratch, and minimal ``/proc``+``/dev``
      are exposed. The canonical repo / host home / creds are simply never bound.
    """
    return [
        exe,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONPATH", "/candidate",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/bin", "/bin",
        "--ro-bind-try", "/sbin", "/sbin",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/work",
        "--ro-bind", candidate_dir, "/candidate",
        "--ro-bind", test_dir, "/test",
        "--chdir", "/work",
        "python3", "/test/harness.py",
    ]


def run_frozen_tests(
    *,
    candidate_files: dict,
    harness_source: str,
    timeout_seconds: int,
) -> SandboxEvidence:
    """Run the frozen ``harness_source`` against ``candidate_files`` in a bwrap sandbox.

    ``candidate_files`` maps relative path -> text content (reconstructed by the trusted
    kernel from durable canonical state — never from the live worker). ``harness_source``
    is the frozen, kernel-owned test harness; it must import the candidate from
    ``/candidate`` and print exactly one JSON line
    ``{"result": "PASS"|"FAIL", "total": int, "passed": int}`` as its machine result.
    Returns runner-owned :class:`SandboxEvidence`. Raises nothing for candidate faults —
    a crash/timeout/non-zero exit is captured as evidence, not an exception.
    """
    exe = bwrap_path()
    cand_hash = canonical_candidate_hash(candidate_files)
    test_hash = _sha256_text(harness_source)
    bver = bwrap_version()
    base = dict(runner_kind=RUNNER_KIND, runner_version=RUNNER_VERSION, bwrap_version=bver,
                candidate_sha256=cand_hash, test_sha256=test_hash, timeout_seconds=timeout_seconds)
    if not exe:
        return SandboxEvidence(terminal_state=SPAWN_FAILED, exit_code=None, harness_result="NONE",
                               tests_total=None, tests_passed=None, detail="bwrap not available", **base)

    with tempfile.TemporaryDirectory(prefix="aidan-verify-") as tmp:
        tmp = Path(tmp)
        cdir = tmp / "candidate"
        tdir = tmp / "test"
        cdir.mkdir()
        tdir.mkdir()
        for rel, content in candidate_files.items():
            p = (cdir / rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        (tdir / "harness.py").write_text(harness_source, encoding="utf-8")

        argv = _bwrap_argv(exe, str(cdir), str(tdir))
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,   # own process group -> whole tree killable on timeout
            )
        except Exception as exc:  # noqa: BLE001
            return SandboxEvidence(terminal_state=SPAWN_FAILED, exit_code=None, harness_result="NONE",
                                   tests_total=None, tests_passed=None,
                                   detail=f"spawn failed: {type(exc).__name__}", **base)
        try:
            out, _err = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()
            return SandboxEvidence(terminal_state=TIMEOUT, exit_code=None, harness_result="NONE",
                                   tests_total=None, tests_passed=None, detail="timeout", **base)

        exit_code = proc.returncode
        result, total, passed = _parse_harness_output(out)
        return SandboxEvidence(terminal_state=COMPLETED, exit_code=exit_code, harness_result=result,
                               tests_total=total, tests_passed=passed, detail="", **base)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _parse_harness_output(out: str):
    """Extract the last JSON result line the frozen harness printed. Machine-owned."""
    result, total, passed = "NONE", None, None
    for line in (out or "").splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if "result" in obj:
            result = "PASS" if obj.get("result") == "PASS" else "FAIL"
            total = obj.get("total") if isinstance(obj.get("total"), int) else total
            passed = obj.get("passed") if isinstance(obj.get("passed"), int) else passed
    return result, total, passed
