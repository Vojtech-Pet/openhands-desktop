"""Real tool implementations, sandboxed to a single workspace directory.

Every tool that takes a path resolves it relative to the workspace and
refuses to touch anything outside it -- the control loop trusts the model's
arguments, so this is the actual enforcement boundary.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 300_000
MAX_OUTPUT_CHARS = 20_000
DEFAULT_COMMAND_TIMEOUT_S = 60


class PathEscapesWorkspaceError(Exception):
    pass


@dataclass
class ToolResult:
    ok: bool
    output: str

    def __str__(self) -> str:
        return self.output


class Toolbox:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.exists():
            raise FileNotFoundError(f"workspace does not exist: {self.workspace}")

    def _resolve(self, path: str) -> Path:
        candidate = (self.workspace / path).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise PathEscapesWorkspaceError(
                f"path {path!r} resolves outside workspace {self.workspace}"
            )
        return candidate

    # -- tools ---------------------------------------------------------

    def read_file(self, path: str) -> ToolResult:
        try:
            target = self._resolve(path)
        except PathEscapesWorkspaceError as exc:
            return ToolResult(False, f"ERROR: {exc}")
        if not target.exists():
            return ToolResult(False, f"ERROR: file not found: {path}")
        if not target.is_file():
            return ToolResult(False, f"ERROR: not a file: {path}")
        data = target.read_bytes()
        truncated = len(data) > MAX_READ_BYTES
        text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += f"\n\n[... truncated, file is {len(data)} bytes, showing first {MAX_READ_BYTES}]"
        return ToolResult(True, text)

    def write_file(self, path: str, content: str) -> ToolResult:
        try:
            target = self._resolve(path)
        except PathEscapesWorkspaceError as exc:
            return ToolResult(False, f"ERROR: {exc}")
        before_existed = target.exists()
        before_size = target.stat().st_size if before_existed else 0
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        after_size = target.stat().st_size
        verb = "updated" if before_existed else "created"
        return ToolResult(
            True, f"{verb} {path} ({before_size} -> {after_size} bytes)"
        )

    def list_dir(self, path: str = ".") -> ToolResult:
        try:
            target = self._resolve(path)
        except PathEscapesWorkspaceError as exc:
            return ToolResult(False, f"ERROR: {exc}")
        if not target.exists():
            return ToolResult(False, f"ERROR: directory not found: {path}")
        if not target.is_dir():
            return ToolResult(False, f"ERROR: not a directory: {path}")
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for entry in entries:
            marker = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{marker}")
        return ToolResult(True, "\n".join(lines) if lines else "(empty directory)")

    def grep(self, pattern: str, path: str = ".") -> ToolResult:
        try:
            target = self._resolve(path)
        except PathEscapesWorkspaceError as exc:
            return ToolResult(False, f"ERROR: {exc}")
        try:
            proc = subprocess.run(
                ["grep", "-rn", "--include=*", "-I", pattern, str(target)],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "ERROR: grep timed out")
        output = proc.stdout or ""
        if proc.returncode not in (0, 1):
            return ToolResult(False, f"ERROR: grep failed: {proc.stderr[:500]}")
        if not output.strip():
            return ToolResult(True, "(no matches)")
        return ToolResult(True, _truncate(output))

    def glob(self, pattern: str) -> ToolResult:
        matches = sorted(str(p.relative_to(self.workspace)) for p in self.workspace.glob(pattern))
        if not matches:
            return ToolResult(True, "(no matches)")
        return ToolResult(True, "\n".join(matches))

    def run_command(self, command: str, timeout: int = DEFAULT_COMMAND_TIMEOUT_S) -> ToolResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"ERROR: command timed out after {timeout}s")
        combined = (proc.stdout or "") + (proc.stderr or "")
        header = f"[exit code {proc.returncode}]\n"
        return ToolResult(proc.returncode == 0, header + _truncate(combined))

    def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        handler = {
            "read_file": lambda a: self.read_file(a["path"]),
            "write_file": lambda a: self.write_file(a["path"], a.get("content", "")),
            "list_dir": lambda a: self.list_dir(a.get("path", ".")),
            "grep": lambda a: self.grep(a["pattern"], a.get("path", ".")),
            "glob": lambda a: self.glob(a["pattern"]),
            "run_command": lambda a: self.run_command(
                a["command"], a.get("timeout", DEFAULT_COMMAND_TIMEOUT_S)
            ),
        }.get(tool_name)
        if handler is None:
            return ToolResult(False, f"ERROR: unknown tool {tool_name!r}")
        try:
            return handler(arguments)
        except KeyError as exc:
            return ToolResult(False, f"ERROR: missing required argument {exc}")
        except Exception as exc:  # noqa: BLE001 -- surfaced to the model as a tool error, not a crash
            return ToolResult(False, f"ERROR: {type(exc).__name__}: {exc}")


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n\n[... truncated, {len(text)} chars total]"


TOOL_SCHEMAS = {
    "read_file": {"path": "string (relative to workspace)"},
    "write_file": {"path": "string (relative to workspace)", "content": "string (full file content)"},
    "list_dir": {"path": "string, optional, default '.'"},
    "grep": {"pattern": "string (regex)", "path": "string, optional, default '.'"},
    "glob": {"pattern": "string (glob pattern, e.g. '**/*.py')"},
    "run_command": {"command": "string (shell command)", "timeout": "int seconds, optional"},
}
