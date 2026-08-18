"""Typed, provider-neutral worker boundary.

A WorkerAdapter is a replaceable specialist executor. It receives a bounded
``WorkerRequest`` (never a database connection or credential) and returns a
``WorkerResult`` — a CLAIM and result data only. A WorkerResult never carries a
canonical success verdict; only the deterministic verifier (a later slice) can
authorize a Proof Receipt. Provider identity is recorded as ``worker_kind`` /
``worker_version`` provenance and never defines system semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkerRequest:
    """Bounded execution data handed to a worker. Contains no DB access."""

    action_request_id: str
    attempt_id: str
    venture_id: str
    spec_hash: str
    worker_kind: str
    task_payload: dict[str, Any]
    declared_inputs: dict[str, Any]
    capabilities: tuple[str, ...]
    timeout_seconds: int
    workspace_ref: str
    expected_output_contract: dict[str, Any]


@dataclass(frozen=True)
class WorkerResult:
    """A worker's claimed outcome + result data. NOT canonical success.

    ``reported_outcome`` is a claim only (deliberately not ``status=SUCCEEDED``).
    ``external_result_id`` gives the result a deterministic identity for canonical
    dedup. ``artifacts`` are declarations for a later slice; they carry no
    authority here.
    """

    worker_kind: str
    external_result_id: str
    reported_outcome: str
    worker_version: str = "0"
    structured_output: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    logs_ref: Optional[str] = None
    failure_metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class WorkerAdapter(Protocol):
    """Replaceable executor. Implementations receive no database connection."""

    kind: str

    def execute(self, request: WorkerRequest) -> WorkerResult:  # pragma: no cover - protocol
        ...


class WorkerRegistry:
    """Smallest in-process replaceability mechanism: worker_kind -> adapter.

    Not a plugin platform, marketplace, or dynamic loader.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, WorkerAdapter] = {}

    def register(self, adapter: WorkerAdapter) -> None:
        self._adapters[adapter.kind] = adapter

    def get(self, worker_kind: str) -> WorkerAdapter:
        try:
            return self._adapters[worker_kind]
        except KeyError:
            raise KeyError(f"no worker adapter registered for kind {worker_kind!r}") from None
