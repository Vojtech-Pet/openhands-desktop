"""OpenHands app-server entry point with desktop project agents registered."""

from __future__ import annotations

import os
from pathlib import Path

from openhands.sdk.subagent import register_file_agents


def _register_desktop_project_agents() -> None:
    raw_paths = os.getenv("OPENHANDS_DESKTOP_AGENT_PROJECTS", "")
    for raw_path in raw_paths.split(os.pathsep):
        project_path = Path(raw_path).expanduser()
        if raw_path and project_path.is_dir():
            register_file_agents(project_path)


_register_desktop_project_agents()

# Import only after file agents are registered. The app-server later adds
# built-ins without replacing these project-specific definitions.
from openhands.server.listen import app  # noqa: E402


__all__ = ["app"]
