"""The supervising control loop: hashing/dedup, progress scoring, and the
verifier. The model proposes one action per turn; everything here decides
whether that action is allowed to run, and whether the run as a whole is
making progress at all.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# --- limits (point 8 of the design: separate knobs, not one token cap) ----
MAX_REASONING_TOKENS = 4096
MAX_OUTPUT_TOKENS = 8192
MAX_AGENT_STEPS = 30
MAX_TOOL_CALLS = 20
MAX_SAME_RESULT = 3
MAX_AUTO_NUDGES = 1
NO_PROGRESS_WINDOW = 5


def normalize_arguments(tool: str, arguments: dict) -> dict:
    """Collapses argument variants that mean the same call (different path
    spellings, extra whitespace in a command) so they hash identically."""
    result = dict(arguments)
    if "path" in result:
        result["path"] = str(Path(result["path"]).as_posix())
    if "command" in result:
        result["command"] = " ".join(str(result["command"]).split())
    if "pattern" in result:
        result["pattern"] = str(result["pattern"]).strip()
    return result


def normalize_call(tool: str, arguments: dict) -> str:
    payload = {"tool": tool, "arguments": normalize_arguments(tool, arguments)}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def call_hash(tool: str, arguments: dict) -> str:
    return hashlib.sha256(normalize_call(tool, arguments).encode("utf-8")).hexdigest()


def result_hash(result: str) -> str:
    cleaned = " ".join(result.split())
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def evaluate_progress(
    *,
    tool_name: str,
    result_ok: bool,
    duplicate_call: bool,
    duplicate_result: bool,
) -> int:
    """Heuristic progress score for one step. Real semantic understanding of
    "did this actually help" isn't feasible from the tool result alone, so
    this leans on cheap, honest signals: did a write succeed, did a command
    exit 0, is this call/result something we've already seen.
    """
    score = 0
    if tool_name == "write_file" and result_ok:
        score += 2
    elif tool_name == "run_command" and result_ok:
        score += 1
    elif result_ok:
        score += 1  # first-time read/grep/list that returned something new

    if duplicate_result:
        score -= 1
    if duplicate_call:
        score -= 2
    return score


@dataclass
class VerificationResult:
    success: bool
    reason: str


def verify_task(workspace: Path) -> VerificationResult:
    """Best-effort verifier: checks a test suite if one exists, and that the
    working tree actually changed. Both checks degrade to "skipped" (not a
    hard failure) when they don't apply, since a generic verifier can't know
    the task's real acceptance criteria -- it can only refuse to trust a
    FINISH that made literally no changes and has a failing test suite.
    """
    reasons = []

    tests_result = _run_tests_if_present(workspace)
    if tests_result is False:
        return VerificationResult(False, "Testy zlyhali (pytest exit code != 0).")
    reasons.append("testy: OK" if tests_result else "testy: preskočené (žiadna sada nenájdená)")

    diff_result = _check_git_diff(workspace)
    if diff_result is False:
        return VerificationResult(
            False, "Git repozitár existuje, ale working tree nemá žiadne zmeny."
        )
    reasons.append("git diff: OK" if diff_result else "git diff: preskočené (nie je git repo)")

    return VerificationResult(True, "; ".join(reasons))


def _run_tests_if_present(workspace: Path) -> bool | None:
    """True = tests ran and passed, False = tests ran and failed, None = no
    test suite detected (verifier should not block on this)."""
    has_tests = (workspace / "tests").is_dir() or any(workspace.glob("test_*.py")) or any(
        workspace.glob("*_test.py")
    )
    has_pytest_config = (
        (workspace / "pytest.ini").exists()
        or (workspace / "pyproject.toml").exists()
        or (workspace / "setup.cfg").exists()
    )
    if not (has_tests or has_pytest_config):
        return None
    try:
        proc = subprocess.run(
            ["python3", "-m", "pytest", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode == 5:  # pytest: no tests collected
        return None
    return proc.returncode == 0


def _check_git_diff(workspace: Path) -> bool | None:
    if not (workspace / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
        )
        staged = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return bool(proc.stdout.strip()) or bool(staged.stdout.strip())
