"""Bubblewrap sandbox CONTRACT proof (Linux/CI). Proves — experimentally, not from
memory — that the narrow profile in ``aidan_core.factory.test_execution`` actually
enforces the containment properties required to run untrusted candidate code.

Each probe is a FROZEN harness that attempts one escape and reports whether it was
CONTAINED (``result=PASS`` means containment held). Also proves timeout kills the tree.

Usage: ``python verify_sandbox_contract.py <checkout_dir>``. Exit 0 iff bwrap is present
and every containment + timeout property holds. Run under the installed wheel or source.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from aidan_core.factory import test_execution as te

NOOP = {"noop.py": "# nothing\n"}

H_NETWORK = '''
import json, socket
contained = False
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(3)
    s.connect(("1.1.1.1", 53)); s.close()
except Exception:
    contained = True   # no route / blocked -> network namespace isolated
print(json.dumps({"result": "PASS" if contained else "FAIL", "total": 1, "passed": 1 if contained else 0}))
'''

# The actual host checkout path is injected by the proof; the sandbox must NOT expose it.
H_REPO_WRITE_TMPL = '''
import json, os
target = {target!r}
contained = False
try:
    with open(os.path.join(target, "AIDAN_SANDBOX_ESCAPE_SENTINEL"), "w") as f:
        f.write("escaped")
except Exception:
    contained = True   # path not present / not writable inside the sandbox
print(json.dumps({{"result": "PASS" if contained else "FAIL", "total": 1, "passed": 1 if contained else 0}}))
'''

H_SECRET = '''
import json, os
leaked = os.environ.get("AIDAN_TEST_SECRET")
home = os.environ.get("HOME")
contained = leaked is None and home == "/work"
print(json.dumps({"result": "PASS" if contained else "FAIL", "total": 1, "passed": 1 if contained else 0}))
'''

H_SCRATCH = '''
import json, os
ok = False
try:
    p = "/work/scratch.txt"
    open(p, "w").write("ok")
    ok = open(p).read() == "ok"
except Exception:
    ok = False
print(json.dumps({"result": "PASS" if ok else "FAIL", "total": 1, "passed": 1 if ok else 0}))
'''

H_TIMEOUT = '''
import time
time.sleep(60)
'''


def _run(harness, timeout=20):
    return te.run_frozen_tests(candidate_files=NOOP, harness_source=harness, timeout_seconds=timeout)


def main() -> int:
    checkout = str(Path(sys.argv[1]).resolve()) if len(sys.argv) > 1 else os.getcwd()
    print(f"bwrap available: {te.bwrap_available()}  version: {te.bwrap_version()}")
    if not te.bwrap_available():
        print("FAIL: bwrap not available"); return 1

    fails = []

    def expect_contained(name, harness, *, secret=False):
        if secret:
            os.environ["AIDAN_TEST_SECRET"] = "supersecret-should-not-leak"
        ev = _run(harness)
        if secret:
            os.environ.pop("AIDAN_TEST_SECRET", None)
        ok = ev.terminal_state == te.COMPLETED and ev.exit_code == 0 and ev.harness_result == "PASS"
        print(f"  {'OK  ' if ok else 'FAIL'} {name}: state={ev.terminal_state} exit={ev.exit_code} "
              f"harness={ev.harness_result} detail={ev.detail}")
        if not ok:
            fails.append(name)

    expect_contained("network_isolated", H_NETWORK)
    expect_contained("canonical_repo_not_writable", H_REPO_WRITE_TMPL.format(target=checkout))
    expect_contained("host_secret_and_home_contained", H_SECRET, secret=True)
    expect_contained("writable_scratch", H_SCRATCH)

    ev = _run(H_TIMEOUT, timeout=3)
    ok_to = ev.terminal_state == te.TIMEOUT
    print(f"  {'OK  ' if ok_to else 'FAIL'} timeout_kills_tree: state={ev.terminal_state} detail={ev.detail}")
    if not ok_to:
        fails.append("timeout")

    if fails:
        print(f"\nFAIL: sandbox contract not satisfied: {fails}")
        return 1
    print("\nPASS: bwrap sandbox contract enforced (network, repo-write, secret/home, scratch, timeout)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
