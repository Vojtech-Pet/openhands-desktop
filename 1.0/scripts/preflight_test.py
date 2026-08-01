"""Real test of workspace_preflight against actual directories with real
permission bits and real setfacl-applied ACLs -- not mocked stat() calls.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openhands_desktop.core.workspace_preflight import run_preflight, SANDBOX_UID


def report(label: str, path: str) -> None:
    result = run_preflight(path)
    print(f"\n=== {label} ({path}) ===")
    print(f"ok = {result.ok}")
    for c in result.checks:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.message}")
        if c.suggested_fix:
            print(f"    fix: {c.suggested_fix}")


with tempfile.TemporaryDirectory() as tmp:
    # Case 1: plain 755 dir, no ACL -- should FAIL (not world-writable, no ACL for our uid)
    case1 = os.path.join(tmp, "plain_755")
    os.makedirs(case1, mode=0o755)
    report("plain 755, no ACL", case1)

    # Case 2: with the documented setfacl workaround applied -- should PASS
    case2 = os.path.join(tmp, "with_acl")
    os.makedirs(case2, mode=0o755)
    subprocess.run(
        ["setfacl", "-R", "-d", "-m", f"u:{SANDBOX_UID}:rwX", case2], check=True
    )
    subprocess.run(["setfacl", "-m", f"u:{SANDBOX_UID}:rwx", case2], check=True)
    report("with setfacl workaround applied", case2)

    # Case 3: world-writable (777) -- should PASS via the 'other' bit fallback.
    # os.makedirs' mode= is subject to umask, so chmod explicitly afterward.
    case3 = os.path.join(tmp, "world_writable")
    os.makedirs(case3)
    os.chmod(case3, 0o777)
    report("world-writable 777, no ACL", case3)

    # Case 4: a fake git repo inside the ACL-fixed dir -- should surface the
    # informational git-ownership notice without failing overall.
    case4 = os.path.join(case2, "fake_repo")
    os.makedirs(os.path.join(case4, ".git"))
    report("git repo inside ACL-fixed dir", case4)

    # Case 5: nonexistent path
    report("nonexistent path", os.path.join(tmp, "does_not_exist"))
