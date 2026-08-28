"""Regression guard for the artifact-completeness checker itself.

The clean-install wheel proof lives in ``packages/core/tools/verify_wheel_packaging.py``
and runs as the CI ``packaging-artifact`` job. THIS test (pure, no DB, no wheel build)
proves the checker's package-content criterion is driven by dynamic source-tree
discovery — not a hand-maintained list that silently drifted and once omitted
``aidan_core.alpha`` while still reporting PASS.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPO_ROOT / "packages" / "core"
SCRIPT = PROJECT_DIR / "tools" / "verify_wheel_packaging.py"

EXPECTED_PACKAGES = {
    "aidan_core",
    "aidan_core/alpha",
    "aidan_core/build",
    "aidan_core/deploy",
    "aidan_core/factory",
    "aidan_core/market",
    "aidan_core/migrations",
    "aidan_core/research",
}


def _load_checker():
    spec = importlib.util.spec_from_file_location("verify_wheel_packaging", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dynamic_discovery_finds_every_source_package():
    m = _load_checker()
    assert m.discover_source_packages(PROJECT_DIR) == EXPECTED_PACKAGES


def test_discovery_excludes_pycache_and_non_packages():
    m = _load_checker()
    got = m.discover_source_packages(PROJECT_DIR)
    assert not any("__pycache__" in p for p in got)
    assert all(p == "aidan_core" or p.startswith("aidan_core/") for p in got)


def test_dynamic_guard_catches_missing_alpha():
    # The load-bearing repair: an intended package absent from the wheel is reported.
    m = _load_checker()
    intended = m.discover_source_packages(PROJECT_DIR)
    wheel_without_alpha = intended - {"aidan_core/alpha"}
    assert m.missing_packages(intended, wheel_without_alpha) == ["aidan_core/alpha"]


def test_old_static_list_would_have_false_passed_on_alpha_loss():
    # RED demonstration (deterministic, synthetic): the pre-repair hand-maintained
    # list omitted alpha, so a wheel missing aidan_core/alpha still satisfied it.
    legacy_required = (
        "aidan_core", "aidan_core/research", "aidan_core/factory",
        "aidan_core/build", "aidan_core/deploy", "aidan_core/market",
    )
    wheel_without_alpha = set(legacy_required)  # alpha never listed, so never checked
    legacy_missing = [p for p in legacy_required if p not in wheel_without_alpha]
    assert legacy_missing == []  # false pass under the old guard
    # The repaired dynamic guard, by contrast, WOULD catch it:
    m = _load_checker()
    intended = m.discover_source_packages(PROJECT_DIR)
    assert "aidan_core/alpha" in m.missing_packages(intended, wheel_without_alpha)
