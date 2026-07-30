"""Checks a candidate host workspace directory for the uid/gid mismatch
problems documented in local-findings/issue-05-workspace-uid-gid-mismatch.md:
the agent-server container runs as a fixed uid inside the sandbox, and a
bind-mounted host directory that isn't writable by that uid (or that later
becomes host-git-hostile once the agent has written to it) causes confusing
in-agent "Permission denied" errors and host-side "dubious ownership" git
failures that don't point back at the real cause.

This can't fully simulate "would uid 10001 be able to write here" without
being uid 10001 -- there is no user of that uid on the host, so `os.access`
against the current process's uid is useless here. Instead: parse the
directory's actual POSIX ACL (via `getfacl`, since no py ACL library is
installed and shelling out is simpler than adding a dependency for this one
check) for an explicit `user:{uid}:...w...` entry, and fall back to the
standard `other` permission bit as a conservative signal when no such ACL
entry exists.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

SANDBOX_UID = 10001


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    message: str
    suggested_fix: str | None = None


@dataclass
class PreflightResult:
    path: str
    ok: bool
    checks: list[PreflightCheck] = field(default_factory=list)


def _parse_acl_write_access(path: str, uid: int) -> bool | None:
    """Returns True/False if an explicit ACL entry for `uid` is found (either
    effective or default/inherited), None if `getfacl` isn't available or no
    such entry exists (caller should fall back to standard permission bits).
    """
    try:
        result = subprocess.run(
            ["getfacl", "-p", path], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    pattern = re.compile(rf"^(?:default:)?user:{uid}:([rwx-]{{3}})$", re.MULTILINE)
    matches = pattern.findall(result.stdout)
    if not matches:
        return None
    return any(perm[1] == "w" for perm in matches)


def _other_bit_writable(mode: int) -> bool:
    return bool(mode & 0o002)


def run_preflight(path: str) -> PreflightResult:
    checks: list[PreflightCheck] = []

    if not os.path.exists(path):
        checks.append(
            PreflightCheck("path_exists", False, f"{path!r} does not exist")
        )
        return PreflightResult(path=path, ok=False, checks=checks)
    checks.append(PreflightCheck("path_exists", True, "Path exists"))

    if not os.path.isdir(path):
        checks.append(
            PreflightCheck("is_directory", False, f"{path!r} is not a directory")
        )
        return PreflightResult(path=path, ok=False, checks=checks)
    checks.append(PreflightCheck("is_directory", True, "Path is a directory"))

    st = os.stat(path)
    acl_writable = _parse_acl_write_access(path, SANDBOX_UID)
    if acl_writable is True:
        checks.append(
            PreflightCheck(
                "sandbox_uid_writable",
                True,
                f"ACL grants uid {SANDBOX_UID} write access",
            )
        )
    elif acl_writable is False:
        checks.append(
            PreflightCheck(
                "sandbox_uid_writable",
                False,
                f"ACL has an entry for uid {SANDBOX_UID} but it does not grant write",
                suggested_fix=(
                    f"setfacl -R -d -m u:{SANDBOX_UID}:rwX {path}"
                ),
            )
        )
    elif _other_bit_writable(st.st_mode):
        checks.append(
            PreflightCheck(
                "sandbox_uid_writable",
                True,
                "No ACL entry found, but the 'other' permission bit allows "
                f"write access, which covers uid {SANDBOX_UID}",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                "sandbox_uid_writable",
                False,
                f"No ACL entry for uid {SANDBOX_UID} and the 'other' "
                f"permission bit does not allow write (mode "
                f"{oct(st.st_mode & 0o777)}, owned by uid {st.st_uid}). The "
                "sandbox container has no user matching your host account, "
                "so unless this directory is writable by 'other' or has an "
                "explicit ACL entry, the agent will get Permission denied "
                "creating its own files here.",
                suggested_fix=f"setfacl -R -d -m u:{SANDBOX_UID}:rwX {path}",
            )
        )

    if os.path.isdir(os.path.join(path, ".git")):
        checks.append(
            PreflightCheck(
                "git_repo_ownership_notice",
                True,
                "This is a git repository. Once the agent (running as a "
                f"different uid, {SANDBOX_UID}) writes to it, host-side git "
                "commands will likely refuse to run here "
                "('detected dubious ownership') until you add an exception.",
                suggested_fix=f"git config --global --add safe.directory {path}",
            )
        )

    ok = all(c.passed for c in checks)
    return PreflightResult(path=path, ok=ok, checks=checks)
