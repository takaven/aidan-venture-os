"""Artifact-level packaging proof for aidan-core (deterministic; no network beyond pip).

Builds the ``aidan-core`` wheel from ``packages/core``, inspects the wheel's contents,
then installs *exactly that wheel* into a clean virtualenv and imports the representative
runtime packages from a NEUTRAL working directory — proving every intended
``aidan_core[.*]`` package ships in the installed artifact and resolves from
``site-packages``, never from the repository source tree.

This check exists because the normal pytest suite injects ``packages/core`` onto
``sys.path`` (``tests/conftest.py``) and therefore source-shadows the wheel: a green
pytest run does NOT prove the built/installed artifact is complete. This proof cannot be
satisfied by source shadowing — it runs the installed wheel in isolation.

Exit 0 iff: the wheel builds, contains every REQUIRED_WHEEL_PKG, installs cleanly, and
every REQUIRED_IMPORT imports from the installed site-packages (not the repo). Any
omission fails non-zero. Intended to run pre-repair as a RED proof and in CI as a
standing regression guard.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

# The set of packages that MUST be present inside the built wheel is NOT a
# hand-maintained list (which silently drifts — an earlier version omitted
# ``aidan_core.alpha`` and still reported PASS). It is discovered dynamically from
# the source tree: every directory under ``<project_dir>/aidan_core`` that contains
# an ``__init__.py`` is an intended package and must ship in the wheel.

# Representative runtime modules that MUST import from the installed wheel. This is
# a spot-check that complements — never replaces — the dynamic completeness check.
REQUIRED_IMPORT = (
    "aidan_core",
    "aidan_core.research",
    "aidan_core.alpha",
    "aidan_core.factory.workers",
    "aidan_core.factory.runtime",
    "aidan_core.factory.spec",
    "aidan_core.factory.verifiers",
    "aidan_core.build.runtime",
    "aidan_core.build.spec",
    "aidan_core.deploy.runtime",
    "aidan_core.market.postmark",
)


def discover_source_packages(project_dir) -> set[str]:
    """Every intended package under ``<project_dir>/aidan_core``, as wheel paths.

    A package is a directory containing ``__init__.py`` (namespace dirs without one
    are excluded); ``__pycache__`` and build artifacts are ignored. The root package
    ``aidan_core`` is included. Returns forward-slash names matching wheel layout,
    e.g. ``{"aidan_core", "aidan_core/alpha", ...}``.
    """
    project_dir = Path(project_dir)
    root = project_dir / "aidan_core"
    pkgs: set[str] = set()
    for init in root.rglob("__init__.py"):
        parts = init.parent.relative_to(project_dir).parts
        if "__pycache__" in parts:
            continue
        pkgs.add("/".join(parts))
    return pkgs


def missing_packages(intended, present) -> list[str]:
    """Intended packages absent from the built wheel (sorted)."""
    return sorted(set(intended) - set(present))


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def _venv_python(venv_dir: Path) -> Path:
    # Windows lays out Scripts/python.exe; POSIX lays out bin/python.
    win = venv_dir / "Scripts" / "python.exe"
    return win if win.exists() else venv_dir / "bin" / "python"


def _wheel_packages(whl: Path) -> set[str]:
    with zipfile.ZipFile(whl) as z:
        dirs = set()
        for name in z.namelist():
            if name.endswith(".py"):
                dirs.add(name.rsplit("/", 1)[0] if "/" in name else "")
        return dirs


def main() -> int:
    project_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    if not (project_dir / "pyproject.toml").is_file():
        print(f"FAIL: no pyproject.toml under {project_dir}")
        return 2
    print(f"project_dir = {project_dir}")

    with tempfile.TemporaryDirectory(prefix="aidan_pkgproof_") as tmp:
        tmp = Path(tmp)
        wheels = tmp / "wheels"
        wheels.mkdir()

        # 1. Build ONLY the aidan-core wheel (deps resolved later at install time).
        _run([sys.executable, "-m", "pip", "wheel", str(project_dir),
              "--no-deps", "--wheel-dir", str(wheels)])
        built = sorted(wheels.glob("aidan_core-*.whl"))
        if not built:
            print("FAIL: no aidan_core wheel was produced")
            return 1
        whl = built[0]
        print(f"built wheel = {whl.name}")

        # 2. Inspect wheel contents against the COMPLETE dynamically-discovered
        #    source-package tree (not a hand-maintained list).
        intended = discover_source_packages(project_dir)
        print("intended source packages (discovered):\n  " + "\n  ".join(sorted(intended)))
        pkg_dirs = _wheel_packages(whl)
        print("wheel package dirs:\n  " + "\n  ".join(sorted(p for p in pkg_dirs if p)))
        missing_pkgs = missing_packages(intended, pkg_dirs)
        if missing_pkgs:
            print(f"FAIL: wheel is MISSING source packages: {missing_pkgs}")
            return 1
        print(f"OK: wheel contains every discovered source package ({len(intended)} packages)")

        # 3. Clean isolated venv + install exactly that wheel.
        venv_dir = tmp / "venv"
        _run([sys.executable, "-m", "venv", str(venv_dir)])
        vpy = _venv_python(venv_dir)
        _run([str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "-q", str(whl)])

        # 4/5/6. Import each required module from a NEUTRAL cwd; capture origins.
        neutral = tmp / "neutral"
        neutral.mkdir()
        stdlib = sysconfig.get_paths()["stdlib"]
        importer = neutral / "_import_probe.py"
        importer.write_text(
            "import importlib, json, sys\n"
            f"required = {json.dumps(REQUIRED_IMPORT)}\n"
            f"repo = {json.dumps(str(project_dir))}.replace(chr(92), '/')\n"
            "out = []\n"
            "for m in required:\n"
            "    rec = {'module': m}\n"
            "    try:\n"
            "        mod = importlib.import_module(m)\n"
            "        origin = getattr(mod, '__file__', None) or '<none>'\n"
            "        rec['ok'] = True; rec['origin'] = origin.replace(chr(92), '/')\n"
            "    except Exception as e:\n"
            "        rec['ok'] = False; rec['error'] = f'{type(e).__name__}: {e}'\n"
            "    out.append(rec)\n"
            "print('PROBE_JSON=' + json.dumps({\n"
            "    'result': out,\n"
            "    'sys_path': [p.replace(chr(92), '/') for p in sys.path],\n"
            "}))\n",
            encoding="utf-8",
        )
        proc = subprocess.run([str(vpy), str(importer)], cwd=str(neutral),
                              text=True, capture_output=True)
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            print("FAIL: import probe crashed")
            return 1
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_JSON=")), None)
        if line is None:
            print("FAIL: no probe output")
            return 1
        data = json.loads(line[len("PROBE_JSON="):])

        # Guard: the repo source tree must NOT be on the isolated interpreter's path.
        repo_norm = str(project_dir).replace("\\", "/")
        shadow = [p for p in data["sys_path"] if p and p == repo_norm]
        if shadow:
            print(f"FAIL: repo source on isolated sys.path (shadowing): {shadow}")
            return 1

        site_root = str(venv_dir).replace("\\", "/")
        failures = []
        for rec in data["result"]:
            if not rec.get("ok"):
                failures.append(f"{rec['module']}: {rec.get('error')}")
                print(f"FAIL import {rec['module']}: {rec.get('error')}")
                continue
            origin = rec["origin"]
            # Origin must come from the installed venv (site-packages), not the repo source.
            if not origin.startswith(site_root) or repo_norm in origin:
                failures.append(f"{rec['module']}: bad origin {origin}")
                print(f"FAIL origin {rec['module']}: {origin} (expected under {site_root})")
            else:
                print(f"OK   {rec['module']}  <- {origin}")

        if failures:
            print(f"\nFAIL: {len(failures)} packaging problem(s)")
            return 1

    print("\nPASS: installed aidan-core artifact is complete and imports from site-packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
