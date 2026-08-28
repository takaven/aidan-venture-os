"""Installed-runtime integrity regressions (pure; no DB).

Guards the two defects fixed in the installed-runtime-integrity slice:
  A. canonical SQL migrations are a single packaged source, discoverable
     independent of cwd / repository checkout;
  B. the canonical-OS-repository workspace guard is installation-independent
     (trusted host identity), not derived from the module's ``__file__`` location.

The full clean-wheel + real-PostgreSQL application proof lives in
``packages/core/tools/verify_installed_runtime.py`` (CI ``installed-runtime`` job);
these are the fast source-level invariants.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aidan_core import migrate
from aidan_core.build import substrate as substrate_mod
from aidan_core.build import workspace as ws
from aidan_core.errors import BuildAuthorityError

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSIONS = [f"{i:04d}" for i in range(1, 26)]


# ---- A. migration packaging / discovery -------------------------------------

def test_migration_set_is_exactly_25_canonical():
    versions = [v for v, *_ in migrate.discover()]
    assert versions == EXPECTED_VERSIONS


def test_migration_ordering_is_stable():
    a = [v for v, *_ in migrate.discover()]
    b = [v for v, *_ in migrate.discover()]
    assert a == b == EXPECTED_VERSIONS


def test_single_authoritative_migration_source():
    # The migrations moved INTO the package; the old repo-root dir must be gone,
    # so there is exactly one canonical source (no drift-prone duplicate).
    assert not (REPO_ROOT / "migrations").exists()
    pkg_dir = Path(migrate.default_migrations_dir())
    assert pkg_dir.name == "migrations" and pkg_dir.parent.name == "aidan_core"
    assert len(list(pkg_dir.glob("[0-9]*.sql"))) == 25


def test_default_discovery_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)              # a cwd with no ``migrations`` dir
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    versions = [v for v, *_ in migrate.discover()]
    assert versions == EXPECTED_VERSIONS


def test_migrations_dir_override_is_honoured(tmp_path, monkeypatch):
    (tmp_path / "0001_x.sql").write_text("SELECT 1;")
    (tmp_path / "0002_y.sql").write_text("SELECT 2;")
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    versions = [v for v, *_ in migrate.discover()]
    assert versions == ["0001", "0002"]


# ---- A2. substrate infrastructure inputs as packaged resources --------------

def test_substrate_root_is_a_single_packaged_source():
    # Moved INTO the package; the old repo-root dir is gone (single source).
    assert not (REPO_ROOT / "substrate").exists()
    root = Path(substrate_mod.default_substrate_root())
    assert root.name == "substrate" and root.parent.name == "aidan_core"
    assert root.is_dir()


def test_substrate_component_files_resolve_from_resources():
    root = substrate_mod.default_substrate_root()
    for component in ("CONFIG_BOUNDARY", "TEST_HARNESS"):
        files = substrate_mod._component_files(root, component)
        assert files, component
        for rel, content in files:
            assert content  # non-empty canonical bytes


def test_substrate_discovery_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    files = substrate_mod._component_files(substrate_mod.default_substrate_root(), "CONFIG_BOUNDARY")
    assert files


# ---- B. installation-independent canonical-repo identity --------------------

def test_source_fallback_identifies_repo_and_rejects_it(monkeypatch):
    monkeypatch.delenv("AIDAN_OS_REPO_ROOT", raising=False)
    # Running from the source checkout, the fallback still identifies the repo root.
    assert ws.canonical_os_repo_root() == REPO_ROOT
    with pytest.raises(BuildAuthorityError):
        ws.assert_isolated_workspace(str(REPO_ROOT))


def test_configured_canonical_root_rejects_repo_and_nested(tmp_path, monkeypatch):
    canonical = tmp_path / "os-repo"
    (canonical / "packages" / "core" / "aidan_core").mkdir(parents=True)
    monkeypatch.setenv("AIDAN_OS_REPO_ROOT", str(canonical))
    assert ws.canonical_os_repo_root() == canonical.resolve()
    with pytest.raises(BuildAuthorityError):
        ws.assert_isolated_workspace(str(canonical))
    with pytest.raises(BuildAuthorityError):
        ws.assert_isolated_workspace(str(canonical / "packages"))


def test_disposable_workspace_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDAN_OS_REPO_ROOT", str(tmp_path / "os-repo"))
    (tmp_path / "os-repo").mkdir()
    disposable = tmp_path / "venture-ws"
    disposable.mkdir()
    ws.assert_isolated_workspace(str(disposable))   # must not raise


def test_worker_supplied_path_cannot_bypass_or_redefine_trusted_root(tmp_path, monkeypatch):
    # The trusted root comes ONLY from host config (env), never from the workspace
    # argument. A worker-controlled ``workspace_ref`` pointing at the canonical repo
    # is therefore rejected, and no argument can redefine what "canonical" means.
    canonical = tmp_path / "os-repo"
    canonical.mkdir()
    monkeypatch.setenv("AIDAN_OS_REPO_ROOT", str(canonical))
    with pytest.raises(BuildAuthorityError):
        ws.assert_isolated_workspace(str(canonical))            # payload can't bypass
    # canonical_os_repo_root takes no argument and ignores the checked path entirely.
    assert ws.canonical_os_repo_root() == canonical.resolve()


def test_fail_closed_when_required_but_unconfigured(monkeypatch):
    # Simulate an installed-wheel-like environment: no env config AND no source
    # sibling -> canonical root unknown -> require_canonical must fail closed.
    monkeypatch.delenv("AIDAN_OS_REPO_ROOT", raising=False)
    monkeypatch.setattr(ws, "canonical_os_repo_root", lambda: None)
    with pytest.raises(BuildAuthorityError):
        ws.assert_isolated_workspace("/tmp/whatever", require_canonical=True)
    # ...but a disposable workspace remains acceptable in the permissive default.
    ws.assert_isolated_workspace("/tmp/whatever")
