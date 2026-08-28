"""Minimal Venture Substrate — reusable infrastructure identity (Gate 5 Slice 2).

The substrate provides genuinely repeated INFRASTRUCTURE (configuration boundary,
test-harness convention), never product design. Its source lives inside the
canonical OS monorepo at bounded, allowlisted paths; a ``substrate_release`` freezes
the exact source commit (``source_sha``), the finite selected components, and a
deterministic ``content_hash`` over the ACTUAL component file hashes — so identity
is real provenance, not a free-form version string.

``materialize_substrate`` copies only the selected component files into a venture
workspace (root-contained). A deterministic scope check (``assert_substrate_scope``)
guarantees the substrate carries no product-template material (dashboards,
navigation, pricing, onboarding, chat UI, brand/copy). That is substrate-scope
validation — NOT the Slice 3 AntiGeneric product decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Optional

from psycopg.types.json import Json

from .. import audit, db
from ..actions import canonical_payload_hash
from ..errors import BuildAuthorityError, IdempotencyConflictError, NotFoundError
from . import workspace as ws

# Finite component vocabulary and the bounded source subdirectory for each.
SUBSTRATE_COMPONENTS: dict[str, str] = {
    "CONFIG_BOUNDARY": "config_boundary",
    "TEST_HARNESS": "test_harness",
}

# Path/name fragments that would make the substrate a product template rather than
# infrastructure. Deterministic substrate-scope guard (not the AntiGeneric gate).
FORBIDDEN_SUBSTRATE_FRAGMENTS = (
    "dashboard", "pricing", "onboarding", "navbar", "navigation", "landing",
    "chat", "kpi", "hero", "testimonial", "brand", "logo", "checkout",
)
# Only these infrastructure content types belong in the substrate.
_ALLOWED_SUBSTRATE_SUFFIXES = (".json", ".md", ".toml", ".ini", ".cfg", ".txt")


def default_substrate_root() -> Path:
    """The canonical substrate source directory, installation-independently.

    The substrate infrastructure inputs are the single source of truth shipped as
    package resources (``aidan_core/substrate/<component>/``), resolved via
    ``importlib.resources`` so the installed runtime (build/deploy) locates them
    with no repository checkout and no cwd/``__file__``-location dependence. In an
    ordinary (unpacked) install these are a real directory, so the existing
    ``Path`` traversal in :func:`_component_files` works unchanged.
    """
    return Path(resources.files("aidan_core.substrate"))


@dataclass(frozen=True)
class SubstrateReleaseResult:
    substrate_release_id: str
    content_hash: str
    created: bool


def _component_files(source_root: Path, component: str) -> list[tuple[str, bytes]]:
    comp_dir = source_root / SUBSTRATE_COMPONENTS[component]
    if not comp_dir.is_dir():
        raise NotFoundError(f"substrate component source missing: {comp_dir}")
    files: list[tuple[str, bytes]] = []
    for path in sorted(comp_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(source_root).as_posix()
            files.append((rel, path.read_bytes()))
    return files


def assert_substrate_scope(source_root: Path, components: list) -> None:
    """Guarantee the selected substrate components carry only infrastructure files."""
    for component in components:
        for rel, _content in _component_files(source_root, component):
            low = rel.lower()
            if any(frag in low for frag in FORBIDDEN_SUBSTRATE_FRAGMENTS):
                raise BuildAuthorityError(
                    f"substrate file {rel!r} looks like product-template material, not infrastructure"
                )
            if Path(rel).suffix.lower() not in _ALLOWED_SUBSTRATE_SUFFIXES:
                raise BuildAuthorityError(
                    f"substrate file {rel!r} is not an allowed infrastructure content type"
                )


def _build_component_manifest(source_root: Path, components: list) -> list[dict]:
    manifest: list[dict] = []
    for component in components:
        for rel, content in _component_files(source_root, component):
            manifest.append({
                "component": component, "path": rel,
                "sha256": ws.hash_bytes(content), "size": len(content),
            })
    manifest.sort(key=lambda e: (e["component"], e["path"]))
    return manifest


def compute_release_hash(*, source_repository_ref, source_sha, components, component_manifest) -> str:
    return canonical_payload_hash({
        "source_repository_ref": source_repository_ref,
        "source_sha": source_sha,
        "components": sorted(components),
        "component_manifest": component_manifest,
    })


def create_substrate_release(
    conn,
    *,
    release_key: str,
    source_sha: str,
    components: list,
    source_root: Optional[Path] = None,
    source_repository_ref: str = "takaven/aidan-venture-os",
    actor: str = "factory",
) -> SubstrateReleaseResult:
    """Freeze an immutable substrate release from ACTUAL infrastructure source.

    Idempotent: an identical release_key + identical content converges; a changed
    source SHA, component selection, or component content under the same release_key
    is a deterministic :class:`IdempotencyConflictError`.
    """
    if not source_sha or not source_sha.strip():
        raise ValueError("source_sha is required (exact substrate source provenance)")
    comps = list(components or [])
    if not comps:
        raise ValueError("a substrate release must select at least one component")
    unknown = set(comps) - set(SUBSTRATE_COMPONENTS)
    if unknown:
        raise ValueError(f"unknown substrate components {sorted(unknown)}; allowed: {sorted(SUBSTRATE_COMPONENTS)}")

    root = source_root or default_substrate_root()
    assert_substrate_scope(root, comps)
    component_manifest = _build_component_manifest(root, comps)
    content_hash = compute_release_hash(
        source_repository_ref=source_repository_ref, source_sha=source_sha,
        components=comps, component_manifest=component_manifest,
    )

    with db.transaction(conn) as cur:
        cur.execute("SELECT id, content_hash FROM substrate_release WHERE release_key = %s", (release_key,))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != content_hash:
                raise IdempotencyConflictError(
                    f"substrate_release {release_key!r} already exists with different source/component content"
                )
            return SubstrateReleaseResult(existing[0], content_hash, created=False)
        cur.execute(
            """
            INSERT INTO substrate_release
                (release_key, source_repository_ref, source_sha, components, component_manifest, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (release_key, source_repository_ref, source_sha, Json(sorted(comps)),
             Json(component_manifest), content_hash),
        )
        release_id = cur.fetchone()[0]
        audit.record_event(
            cur, event_type="build.substrate_release_created", actor=actor, venture_id=None,
            action_id=None, payload={"substrate_release_id": str(release_id), "release_key": release_key,
                                     "content_hash": content_hash},
        )
    return SubstrateReleaseResult(release_id, content_hash, created=True)


def get_substrate_release(conn, substrate_release_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, release_key, source_repository_ref, source_sha, components, "
            "component_manifest, content_hash, created_at FROM substrate_release WHERE id = %s",
            (substrate_release_id,),
        )
        return cur.fetchone()


def materialize_substrate(release_row, workspace_root: str, *, source_root: Optional[Path] = None) -> list[dict]:
    """Copy the release's selected component files into the venture workspace (contained).

    Content comes from the exact release source; the written file hashes must match
    the release's component_manifest (verified later by SUBSTRATE_PROVENANCE). Returns
    kernel-computed file entries tagged ``origin='substrate'``.
    """
    components = list(release_row[4] or [])
    root = source_root or default_substrate_root()
    ws.assert_isolated_workspace(workspace_root)
    entries: list[dict] = []
    for component in components:
        for rel, content in _component_files(root, component):
            meta = ws.write_candidate(workspace_root, rel, content)
            meta["origin"] = "substrate"
            entries.append(meta)
    return entries
