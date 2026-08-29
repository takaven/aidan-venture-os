"""Installed-runtime integrity proof for aidan-core (deterministic; needs pip + optional PostgreSQL).

Goes beyond import-completeness (see ``verify_wheel_packaging.py``): it proves the
*installed artifact* can perform load-bearing runtime operations with NO repository
checkout and NO cwd convention. From a clean wheel installed into a fresh venv, run
from a neutral directory, it asserts:

  A. representative ``aidan_core`` modules resolve from installed site-packages;
  B. the installed runtime discovers exactly migrations 0001..0025 from packaged
     resources (not a checkout-relative cwd path);
  C. those migrations actually APPLY against a real PostgreSQL (when DATABASE_URL is
     provided / required) — not merely a list check;
  D. the canonical OS repository path is rejected as a venture workspace through the
     installation-independent trusted-identity mechanism, while a disposable
     workspace is accepted;
  E. no repository source is on the isolated interpreter's path (no source shadowing).

Usage: ``python verify_installed_runtime.py <project_dir> <checkout_dir> [--require-db]``.
With ``--require-db`` (CI) a reachable ``DATABASE_URL`` is mandatory and C must pass;
without it and with no ``DATABASE_URL``, C is skipped (local dev) and clearly reported.
Exit 0 iff every required check passes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _run(cmd, **kw):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def _venv_python(venv_dir: Path) -> Path:
    win = venv_dir / "Scripts" / "python.exe"
    return win if win.exists() else venv_dir / "bin" / "python"


_PROBE = r'''
import json, os, sys, tempfile
from pathlib import Path

checkout = sys.argv[1]
require_db = sys.argv[2] == "1"
norm = lambda p: str(p).replace("\\", "/")
out = {"checks": {}, "errors": []}

def check(name, cond, detail=""):
    out["checks"][name] = bool(cond)
    if not cond:
        out["errors"].append(f"{name}: {detail}")

# E: no repo-source shadowing
check("E_no_source_shadow",
      not any("aidan-venture-os/packages/core" in norm(p) for p in sys.path),
      "repo source on sys.path")

# A: representative modules resolve from site-packages
import importlib
mods = ["aidan_core", "aidan_core.migrate", "aidan_core.factory.runtime",
        "aidan_core.build.workspace", "aidan_core.market.postmark"]
site_ok = True
for m in mods:
    mod = importlib.import_module(m)
    origin = norm(getattr(mod, "__file__", ""))
    if "site-packages/aidan_core" not in origin:
        site_ok = False; out["errors"].append(f"A origin {m}: {origin}")
check("A_site_packages_origin", site_ok)

# B: migration discovery from packaged resources, neutral cwd, no MIGRATIONS_DIR
assert "MIGRATIONS_DIR" not in os.environ
from aidan_core import migrate
disc = migrate.discover()
versions = [v for v, *_ in disc]
mig_dir = norm(migrate.default_migrations_dir())
check("B_discover_0001_0025", versions == [f"{i:04d}" for i in range(1, 26)], str(versions))
check("B_resource_origin_not_cwd",
      "site-packages/aidan_core/migrations" in mig_dir and norm(Path.cwd()) not in mig_dir,
      mig_dir)

# C: real application against PostgreSQL
db_url = os.environ.get("DATABASE_URL")
if db_url:
    import psycopg
    conn = psycopg.connect(db_url, autocommit=True)
    try:
        applied = migrate.apply(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), min(version), max(version) FROM schema_migrations")
            cnt, lo, hi = cur.fetchone()
        check("C_migrations_applied", applied == [f"{i:04d}" for i in range(1, 26)], str(applied))
        check("C_schema_state", cnt == 25 and lo == "0001" and hi == "0025", f"count={cnt} min={lo} max={hi}")
    finally:
        conn.close()
elif require_db:
    check("C_migrations_applied", False, "DATABASE_URL required but not set")
else:
    out["checks"]["C_skipped_no_db"] = True

# D: OS-repo rejection via trusted identity (installation-independent)
from aidan_core.build import workspace as ws
from aidan_core.errors import BuildAuthorityError
# unconfigured fallback under an installed wheel must self-disable
check("D_wheel_fallback_none", ws.canonical_os_repo_root() is None, "fallback not None under wheel")
# fail-closed when required but unconfigured
try:
    ws.assert_isolated_workspace(checkout, require_canonical=True)
    check("D_fail_closed", False, "did not fail closed")
except BuildAuthorityError:
    check("D_fail_closed", True)
# configured -> rejects the real checkout and a nested path
os.environ["AIDAN_OS_REPO_ROOT"] = checkout
rej = 0
for target in (checkout, str(Path(checkout) / "packages" / "core")):
    try:
        ws.assert_isolated_workspace(target)
    except BuildAuthorityError:
        rej += 1
check("D_rejects_canonical_and_nested", rej == 2, f"rejected {rej}/2")
# disposable workspace accepted
try:
    ws.assert_isolated_workspace(tempfile.mkdtemp()); disp = True
except BuildAuthorityError:
    disp = False
check("D_accepts_disposable", disp)

# F: substrate infrastructure inputs available from the installed runtime, and the
# SAME canonical bytes as the single source (build/deploy materialize them).
import hashlib
from aidan_core.build import substrate as sub
sroot = sub.default_substrate_root()
check("F_substrate_resource_origin", "site-packages/aidan_core/substrate" in norm(sroot), norm(sroot))
found = {}
try:
    for comp in ("CONFIG_BOUNDARY", "TEST_HARNESS"):
        for rel, content in sub._component_files(sroot, comp):
            found[rel] = hashlib.sha256(content).hexdigest()
except Exception as e:  # noqa: BLE001
    out["errors"].append(f"F substrate lookup: {type(e).__name__}: {e}")
check("F_substrate_found", len(found) >= 4, f"{len(found)} files")
src = Path(checkout) / "packages" / "core" / "aidan_core" / "substrate"
mismatch = [rel for rel, h in found.items()
            if not (src / rel).is_file() or hashlib.sha256((src / rel).read_bytes()).hexdigest() != h]
check("F_substrate_bytes_match_source", not mismatch, str(mismatch))

# G: the deterministic coding-verification path ships in the wheel and works end to end
# from the installed package (trusted sandbox run -> pure verifier) — correct verifies,
# wrong is rejected. Requires bwrap (the installed-runtime CI job installs it).
from aidan_core.factory import test_execution as _te
from aidan_core.factory.verifiers import TestExecutionVerifier as _TEV, VerificationRequest as _VR
check("G_test_execution_site_packages",
      "site-packages/aidan_core/factory/test_execution" in norm(_te.__file__), norm(_te.__file__))
_HARNESS = ("import json, sys\n"
            "sys.path.insert(0, '/candidate')\n"
            "import candidate\n"
            "t = p = 0\n"
            "for a, b in [(1, 2), (3, 4)]:\n"
            "    t += 1\n"
            "    try:\n"
            "        p += 1 if candidate.add(a, b) == a + b else 0\n"
            "    except Exception:\n"
            "        pass\n"
            "print(json.dumps({'result': 'PASS' if p == t else 'FAIL', 'total': t, 'passed': p}))\n")
if _te.bwrap_available():
    _contract = {"test_execution": {"test_sha256": _te._sha256_text(_HARNESS), "min_tests": 2,
                 "runner_kind": _te.RUNNER_KIND, "runner_version": _te.RUNNER_VERSION}}

    def _verdict(src):
        cf = {"candidate.py": src}
        ev = _te.run_frozen_tests(candidate_files=cf, harness_source=_HARNESS, timeout_seconds=25)
        req = _VR(action_request_id="a", execution_attempt_id="1", verifier_kind="test-execution",
                  expected_output_contract=_contract, worker_structured_output={}, artifacts=(),
                  spec_hash="s", test_evidence=ev.to_dict(),
                  candidate_content_hash=_te.canonical_candidate_hash(cf))
        return _TEV().verify(req).verdict

    check("G_correct_candidate_verifies", _verdict("def add(a, b):\n    return a + b\n") == "VERIFIED")
    check("G_wrong_candidate_rejected", _verdict("def add(a, b):\n    return a * b\n") == "REJECTED")
else:
    out["checks"]["G_skipped_no_bwrap"] = True

print("PROBE_JSON=" + json.dumps(out))
'''


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: verify_installed_runtime.py <project_dir> <checkout_dir> [--require-db]")
        return 2
    project_dir = Path(sys.argv[1]).resolve()
    checkout_dir = Path(sys.argv[2]).resolve()
    require_db = "--require-db" in sys.argv[3:]

    with tempfile.TemporaryDirectory(prefix="aidan_runtime_") as tmp:
        tmp = Path(tmp)
        wheels = tmp / "wheels"; wheels.mkdir()
        _run([sys.executable, "-m", "pip", "wheel", str(project_dir), "--no-deps", "--wheel-dir", str(wheels)])
        whl = sorted(wheels.glob("aidan_core-*.whl"))
        if not whl:
            print("FAIL: no wheel built"); return 1
        whl = whl[0]

        # Sanity: the wheel actually carries the migration resources.
        with zipfile.ZipFile(whl) as z:
            sql = [n for n in z.namelist() if n.endswith(".sql")]
        if len(sql) != 25:
            print(f"FAIL: wheel carries {len(sql)} .sql migrations (expected 25)"); return 1
        print(f"wheel carries {len(sql)} migration resources")

        venv_dir = tmp / "venv"
        _run([sys.executable, "-m", "venv", str(venv_dir)])
        vpy = _venv_python(venv_dir)
        _run([str(vpy), "-m", "pip", "install", "-q", "--disable-pip-version-check", str(whl)])

        neutral = tmp / "neutral"; neutral.mkdir()
        probe = neutral / "_runtime_probe.py"
        probe.write_text(_PROBE, encoding="utf-8")
        env = dict(os.environ)
        env.pop("MIGRATIONS_DIR", None)
        env.pop("AIDAN_OS_REPO_ROOT", None)
        proc = subprocess.run([str(vpy), str(probe), str(checkout_dir), "1" if require_db else "0"],
                              cwd=str(neutral), text=True, capture_output=True, env=env)
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            print("FAIL: probe crashed"); return 1
        line = next((l for l in proc.stdout.splitlines() if l.startswith("PROBE_JSON=")), None)
        if not line:
            print("FAIL: no probe output"); return 1
        data = json.loads(line[len("PROBE_JSON="):])
        for name, ok in data["checks"].items():
            print(f"  {'OK  ' if ok else 'FAIL'} {name}")
        if data["errors"]:
            for e in data["errors"]:
                print(f"  - {e}")
            print("\nFAIL: installed-runtime integrity check failed"); return 1

    print("\nPASS: installed aidan-core runtime is checkout-independent and canonical-repo-safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
