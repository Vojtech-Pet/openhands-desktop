from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx


APP_SERVER_URL = os.getenv("OPENHANDS_APP_SERVER_URL", "http://172.17.0.1:3000")
APP_SERVER_HEALTH_URL = f"{APP_SERVER_URL.rstrip('/')}/health"
OPENHANDS_REPO = Path(os.getenv("OPENHANDS_REPO", "/home/vojtech/Stiahnuté/OpenHands"))
POETRY = Path(os.getenv("OPENHANDS_POETRY", "/home/vojtech/.local/bin/poetry"))
LOG_PATH = Path(os.getenv("OPENHANDS_DESKTOP_SERVER_LOG", "/tmp/openhands-desktop-app-server.log"))
SANDBOX_WEB_URL = os.getenv("OPENHANDS_SANDBOX_WEB_URL", "http://172.17.0.1:3000")
SANDBOX_BIND_HOST = os.getenv("OPENHANDS_SANDBOX_BIND_HOST", "172.17.0.1")
SANDBOX_IMAGE_TAG = os.getenv("AGENT_SERVER_IMAGE_TAG", "1.40.0-python")
SANDBOX_IMAGE_REPOSITORY = os.getenv(
    "AGENT_SERVER_IMAGE_REPOSITORY", "openhands-desktop/agent-server"
)
AGENT_PROJECTS = os.getenv(
    "OPENHANDS_DESKTOP_AGENT_PROJECTS",
    "/home/vojtech/Stiahnuté/rychlik-downloader",
)


_process: asyncio.subprocess.Process | None = None


async def ensure_app_server_running(timeout_s: float = 35.0) -> bool:
    """Start the local OpenHands app-server when the desktop app was launched
    by itself.

    The server still listens on port 3000 for the desktop UI, but OH_WEB_URL
    is set to a Docker-reachable HTTP URL. Without that, OpenHands 1.40 builds
    its default MCP URL as https://... from WEB_HOST or leaves the sandbox with
    an unreachable callback, which makes conversations fail before the agent can
    answer.
    """
    if await _health_ok():
        return True
    if not OPENHANDS_REPO.exists() or not POETRY.exists():
        return False

    global _process
    if _process is None or _process.returncode is not None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = LOG_PATH.open("ab")
        env = os.environ.copy()
        package_src = str(Path(__file__).resolve().parents[1])
        existing_pythonpath = env.get("PYTHONPATH")
        env.update(
            {
                "OH_WEB_URL": SANDBOX_WEB_URL,
                "AGENT_SERVER_IMAGE_REPOSITORY": SANDBOX_IMAGE_REPOSITORY,
                "AGENT_SERVER_IMAGE_TAG": SANDBOX_IMAGE_TAG,
                "OPENHANDS_DESKTOP_AGENT_PROJECTS": AGENT_PROJECTS,
                "PYTHONPATH": (
                    f"{package_src}{os.pathsep}{existing_pythonpath}"
                    if existing_pythonpath
                    else package_src
                ),
            }
        )
        _process = await asyncio.create_subprocess_exec(
            str(POETRY),
            "run",
            "uvicorn",
            "openhands_desktop.managed_app_server:app",
            "--host",
            SANDBOX_BIND_HOST,
            "--port",
            "3000",
            cwd=str(OPENHANDS_REPO),
            env=env,
            stdout=log,
            stderr=log,
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if await _health_ok():
            return True
        if _process.returncode is not None:
            return False
        await asyncio.sleep(0.75)
    return False


async def _health_ok() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(APP_SERVER_HEALTH_URL)
            return 200 <= resp.status_code < 400
    except httpx.HTTPError:
        return False
