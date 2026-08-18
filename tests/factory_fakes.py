"""Deterministic fake workers + helpers for the Gate 4 Slice 1 evals.

Fakes live in test code (not production). They implement the WorkerAdapter
protocol and receive no database connection — they can only return a WorkerResult
(a claim + data). Provider identity is a plain ``kind`` string.
"""
from __future__ import annotations

from aidan_core.factory import spec as spec_mod
from aidan_core.factory.workers import WorkerRegistry, WorkerResult

from conftest import setup_action


class _FakeWorker:
    """A deterministic worker. Records what it was handed; returns a claim only."""

    kind = "fake"

    def __init__(self, *, reported_outcome="success", structured_output=None, artifacts=(), external_suffix="1"):
        self._outcome = reported_outcome
        self._out = structured_output or {}
        self._artifacts = tuple(artifacts)
        self._suffix = external_suffix
        self.calls = 0
        self.last_request = None

    def execute(self, request):  # NB: no connection parameter — workers have no DB authority
        self.calls += 1
        self.last_request = request
        return WorkerResult(
            worker_kind=self.kind,
            external_result_id=f"{self.kind}:{request.action_request_id}:{self._suffix}",
            reported_outcome=self._outcome,
            worker_version="test",
            structured_output=self._out,
            artifacts=self._artifacts,
        )


class FakeWorkerA(_FakeWorker):
    kind = "fake-a"


class FakeWorkerB(_FakeWorker):
    kind = "fake-b"


class ErrorWorker(_FakeWorker):
    """Raises during execute — a WORKER_ERROR. Forged failure metadata is ignored."""

    kind = "fake-a"

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        raise RuntimeError("simulated worker fault")


class FlakyWorker(_FakeWorker):
    """Raises on the first ``fail_first`` calls, then succeeds (worker-error retry)."""

    kind = "fake-a"

    def __init__(self, fail_first=1, structured_output=None, **kw):
        super().__init__(structured_output=structured_output, **kw)
        self._fail_first = fail_first

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        if self.calls <= self._fail_first:
            raise RuntimeError("simulated transient worker fault")
        return WorkerResult(
            worker_kind=self.kind, external_result_id=f"{self.kind}:{request.action_request_id}:{self.calls}",
            reported_outcome="success", worker_version="test", structured_output=self._out,
        )


class SlowWorker(_FakeWorker):
    """Advances an injected fake clock to simulate elapsed time (deterministic timeout)."""

    kind = "fake-a"

    def __init__(self, clock, delay, **kw):
        super().__init__(**kw)
        self._clock = clock
        self._delay = delay

    def execute(self, request):
        self._clock.advance(self._delay)
        return super().execute(request)


class FakeClock:
    """Deterministic monotonic clock for timeout tests (no real sleeps)."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += float(dt)


class ScriptedWorker(_FakeWorker):
    """Returns a different structured_output per attempt (drives retry-then-succeed)."""

    kind = "fake-a"

    def __init__(self, outputs, **kw):
        super().__init__(**kw)
        self._outputs = list(outputs)

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        out = self._outputs[min(self.calls - 1, len(self._outputs) - 1)]
        return WorkerResult(
            worker_kind=self.kind, external_result_id=f"{self.kind}:{request.action_request_id}:{self.calls}",
            reported_outcome="success", worker_version="test", structured_output=out,
        )


def registry_with(*adapters) -> WorkerRegistry:
    reg = WorkerRegistry()
    for a in adapters:
        reg.register(a)
    return reg


def spec_action(
    conn, slug, *, worker_kind="fake-a", verifier_kind="token-match-v1", autonomy_level=1,
    required_autonomy=0, amount=10, grant=100, capabilities=("READ_REPOSITORY",), timeout=60,
    max_attempts=3, task_payload=None, expected_output_contract=None,
):
    """Create a governed ActionRequest and freeze an execution spec for it.

    Returns (venture_id, action_id, spec_result).
    """
    vid, aid = setup_action(
        conn, slug=slug, autonomy_level=autonomy_level, required_autonomy=required_autonomy,
        amount=amount, grant=grant,
    )
    spec = spec_mod.create_execution_spec(
        conn, aid, worker_kind=worker_kind, verifier_kind=verifier_kind,
        timeout_seconds=timeout, max_attempts=max_attempts, capability_scope=list(capabilities),
        task_payload=task_payload or {"goal": "bounded-task"},
        expected_output_contract=expected_output_contract or {"kind": "structured"},
    )
    return vid, aid, spec
