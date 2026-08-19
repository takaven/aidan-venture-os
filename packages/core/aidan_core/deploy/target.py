"""Immutable, venture-owned deployment target identity (Gate 6 Slice 1).

A ``deployment_target`` names an isolated deployment destination for one venture.
It is IDENTITY only — an opaque ``target_ref`` and a provider-neutral
``provider_kind`` — and stores NO credential/secret. Registration is immutable and
idempotent over ALL persisted immutable fields: exact re-registration converges;
any materially changed field is a deterministic conflict (never a silent
re-annotation). Provider names are adapter identity, not architecture.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from psycopg.types.json import Json

from .. import audit, db
from ..errors import IdempotencyConflictError, NotFoundError

# Caller fields that must never carry a credential body into a target identity.
_SECRET_FIELDS = ("token", "api_key", "apikey", "password", "secret", "credential", "credentials")


@dataclass(frozen=True)
class TargetResult:
    deployment_target_id: str
    environment: str
    created: bool


def register_deployment_target(
    conn,
    venture_id: str,
    *,
    environment: str,
    provider_kind: str,
    target_ref: str,
    provenance: Optional[dict[str, Any]] = None,
    actor: str = "factory",
) -> TargetResult:
    """Register the venture's deployment target for an environment. Idempotent.

    Exact re-registration (same environment/provider/ref/provenance) converges; any
    materially changed immutable field is a deterministic
    :class:`IdempotencyConflictError`. No credential body may be stored.
    """
    for name, value in (("environment", environment), ("provider_kind", provider_kind),
                        ("target_ref", target_ref)):
        if not value or not str(value).strip():
            raise ValueError(f"{name} is required")
    provenance = provenance or {}
    leaked = sorted(k for k in provenance if k.lower() in _SECRET_FIELDS)
    if leaked:
        raise ValueError(f"deployment_target stores identity only; refusing secret-like keys {leaked}")

    with db.transaction(conn) as cur:
        cur.execute("SELECT 1 FROM venture WHERE id = %s", (venture_id,))
        if cur.fetchone() is None:
            raise NotFoundError(f"venture {venture_id} does not exist")

        cur.execute(
            "SELECT id, environment, provider_kind, target_ref, provenance "
            "FROM deployment_target WHERE venture_id = %s AND environment = %s",
            (venture_id, environment),
        )
        existing = cur.fetchone()
        if existing is not None:
            if (existing[2], existing[3], existing[4] or {}) != (provider_kind, target_ref, provenance):
                raise IdempotencyConflictError(
                    f"venture {venture_id} already has a {environment!r} target with a different identity"
                )
            return TargetResult(existing[0], existing[1], created=False)

        cur.execute(
            "INSERT INTO deployment_target (venture_id, environment, provider_kind, target_ref, provenance) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (venture_id, environment, provider_kind, target_ref, Json(provenance)),
        )
        target_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="deploy.target_registered", actor=actor, venture_id=venture_id,
            action_id=None, payload={"deployment_target_id": str(target_id), "environment": environment,
                                     "provider_kind": provider_kind},
        )
    return TargetResult(target_id, environment, created=True)


def get_deployment_target(conn, deployment_target_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, venture_id, environment, provider_kind, target_ref, provenance, created_at "
            "FROM deployment_target WHERE id = %s",
            (deployment_target_id,),
        )
        return cur.fetchone()
