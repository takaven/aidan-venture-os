#!/usr/bin/env python3
"""Gate-0-only repository integrity check. Standard library only."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "README.md",
    ".gitignore",
    ".github/workflows/gate0-integrity.yml",
    "docs/architecture/ARCHITECTURE.md",
    "docs/ADR/README.md",
    "docs/ADR/ADR-001-canonical-repository.md",
    "docs/ADR/ADR-002-historical-repositories-are-donors.md",
    "docs/donor-provenance/DONOR_REGISTER.md",
    "docs/donor-provenance/SALVAGE_MANIFEST.md",
    "docs/donor-provenance/SECURITY_AND_LICENSING_REGISTER.md",
    "docs/GATE_0_EXECUTION_RECORD.md",
    "tests/gate0_integrity.py",
}
DEPENDENCY_MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock",
    "go.mod", "Cargo.toml", "Gemfile",
}
RUNTIME_DIRS = ("apps", "services", "packages", "evals", "migrations")
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [p for p in out.decode().split("\0") if p]

def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)

def main() -> int:
    missing = sorted(p for p in REQUIRED if not (ROOT / p).is_file())
    if missing:
        fail("missing required files: " + ", ".join(missing))

    tracked = tracked_files()
    for p in tracked:
        name = Path(p).name
        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            fail(f"forbidden tracked secret-risk file: {p}")
        if name in DEPENDENCY_MANIFESTS:
            fail(f"unexpected dependency manifest: {p}")

    for dirname in RUNTIME_DIRS:
        for p in (ROOT / dirname).rglob("*"):
            if p.is_file() and p.name != ".gitkeep":
                fail(f"future-gate/runtime material present in {dirname}: {p.relative_to(ROOT)}")

    for rel in tracked:
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                fail(f"possible credential/private-key pattern in tracked file: {rel}")

    print(f"PASS: Gate 0 integrity checks ({len(tracked)} tracked files inspected)")
    print("PASS: required documentation present")
    print("PASS: no forbidden tracked .env files")
    print("PASS: no application dependency manifests")
    print("PASS: no runtime/application files in reserved implementation directories")
    print("PASS: no obvious credential/private-key patterns in tracked text")
    return 0

if __name__ == "__main__":
    sys.exit(main())
