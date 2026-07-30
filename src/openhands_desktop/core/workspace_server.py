"""MCP server hosted *inside* the desktop app, giving the agent read access
to a folder on the user's own machine -- same "local HTTP server the sandbox
reaches over the Docker bridge" pattern ask_user_server.py already uses for
its question tool, applied to file access instead.

Why this exists: the OpenHands agent-server API has no per-conversation way
to mount an arbitrary host directory into the sandbox (confirmed in
client.py's DEFAULT_WORKSPACE_PATH note -- the sandbox always gets a fixed
/workspace/project, independent of anything the user types or selects).
Verified live 2026-07-30: a user asking the agent to look at a real host
path sent it hunting for that path *inside its own empty sandbox*, where it
doesn't exist, and it ended up guessing wrong (cloned an unrelated GitHub
repo) and wrecking its own sandbox root trying to "fix" that. This tool
gives the agent an actual, working way to reach a chosen folder instead.

Both connecting a folder and writing to it (2026-07-30: write_file added)
require an explicit user confirmation via on_confirm_connect/on_confirm_write
-- this channel has none of the sandbox's isolation, so unlike a sandbox tool
call, saying yes here is a real grant of access to the user's actual files.
Read access alone (list_folder/read_file) only requires the one connect
confirmation; each write additionally shows the exact path and a preview of
what would be written before it happens.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

# Same bridge address ask_user_server.py and the existing mempalace server
# already use -- reachable from the host and its containers, not the LAN.
DEFAULT_HOST = "172.17.0.1"
DEFAULT_PORT = 8902
MAX_FILE_BYTES = 200_000
MAX_LIST_ENTRIES = 500


class WorkspaceFolderServer:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        on_connected: Callable[[str], None] | None = None,
        on_confirm_connect: Callable[[str], Awaitable[bool]] | None = None,
        on_confirm_write: Callable[[str, str], Awaitable[bool]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._root: Path | None = None
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        # Called (on the qasync loop, same as AskUserServer's on_question --
        # safe to touch Qt widgets directly from it) whenever connect_folder
        # changes the root, so the UI can show what the agent just did
        # instead of it happening invisibly.
        self._on_connected = on_connected
        # Both awaited from inside the MCP tool coroutine, same qasync loop
        # as everything else here -- free to show a real QMessageBox and
        # wait for the click. None means "no confirmation gate configured",
        # which auto-approves (only true for tests; MainWindow always wires
        # a real one).
        self._on_confirm_connect = on_confirm_connect
        self._on_confirm_write = on_confirm_write

        self._mcp = MCPServer(
            name="openhands-desktop-workspace",
            instructions=(
                "Access to a folder on the user's own computer, outside your "
                "normal sandbox. If the user references a path on their machine "
                "(not under /workspace) that you need to look at or write to, "
                "call connect_folder with that path first -- do not guess or "
                "search for it inside your own sandbox, it will not be there. "
                "The user must approve the connection before it succeeds. Then "
                "use list_folder to see what's there and read_file for a "
                "specific file. write_file needs a *separate* user approval per "
                "call (it shows the user the exact content before writing) -- "
                "expect it to be rejected if the change wasn't clearly asked for. "
                "Paths are relative to the connected folder's root -- you cannot "
                "escape it."
            ),
        )
        self._register_tools()

    @property
    def connected(self) -> bool:
        return self._root is not None

    @property
    def root_display(self) -> str:
        return str(self._root) if self._root is not None else ""

    def set_root(self, root: str) -> None:
        self._root = Path(root).expanduser().resolve()

    def _resolve(self, relative: str) -> Path:
        if self._root is None:
            raise ValueError(
                "No folder is connected yet, so there is nothing to look at. "
                "Call connect_folder with the absolute path the user gave you first."
            )
        candidate = (self._root / relative).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(
                f"{relative!r} is outside the connected folder ({self._root}) -- "
                "refusing to go outside it. If you need a different folder, call "
                "connect_folder with that path instead."
            )
        return candidate

    def _register_tools(self) -> None:
        @self._mcp.tool(
            name="connect_folder",
            description=(
                "Connect a folder on the user's own computer so list_folder/"
                "read_file can reach it -- call this first whenever the user "
                "mentions a path on their machine that isn't under /workspace. "
                "`path` should be the absolute path as the user gave it (e.g. "
                "'/home/alice/myproject'). Replaces whichever folder was "
                "connected before, if any."
            ),
        )
        async def connect_folder(path: str) -> str:
            try:
                candidate = Path(path).expanduser().resolve(strict=True)
            except OSError as exc:
                return (
                    f"ERROR: could not connect {path!r} -- it does not exist, or this app "
                    f"does not have permission to see it ({exc}). Double-check the exact "
                    "path with the user; a typo or a path on a different machine than "
                    "this desktop app is running on would both look like this."
                )
            if not candidate.is_dir():
                return (
                    f"ERROR: {path!r} exists but is a file, not a folder -- connect_folder "
                    "needs a directory. If the user wants a specific file, connect its "
                    "parent folder instead and read_file the file from there."
                )
            if self._on_confirm_connect is not None:
                approved = await self._on_confirm_connect(str(candidate))
                if not approved:
                    return (
                        f"DENIED: the user did not approve connecting {candidate}. "
                        "Do not retry this same path -- ask the user what they'd "
                        "like to do instead."
                    )
            self._root = candidate
            if self._on_connected is not None:
                try:
                    self._on_connected(str(candidate))
                except Exception:  # noqa: BLE001 -- UI callback failure shouldn't fail the tool
                    pass
            return f"Connected. list_folder('.') now shows the contents of {candidate}."

        @self._mcp.tool(
            name="list_folder",
            description=(
                "List files and subfolders inside the connected host folder. "
                "`path` is relative to the folder root ('.' for the root itself)."
            ),
        )
        async def list_folder(path: str = ".") -> str:
            try:
                target = self._resolve(path)
            except ValueError as exc:
                return f"ERROR: {exc}"
            if not target.exists():
                return (
                    f"ERROR: {path!r} does not exist inside the connected folder "
                    f"({self._root}). List the parent directory first to see the real "
                    "names instead of guessing."
                )
            if not target.is_dir():
                return f"ERROR: {path!r} is a file, not a directory -- use read_file for it instead."
            try:
                entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError as exc:
                return f"ERROR: found {path!r} but could not read its contents: {exc}"
            lines = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries[:MAX_LIST_ENTRIES]]
            if len(entries) > MAX_LIST_ENTRIES:
                lines.append(f"... and {len(entries) - MAX_LIST_ENTRIES} more")
            return "\n".join(lines) if lines else "(empty)"

        @self._mcp.tool(
            name="read_file",
            description=(
                "Read a text file from the connected host folder. `path` is "
                f"relative to the folder root. Truncated at {MAX_FILE_BYTES} bytes."
            ),
        )
        async def read_file(path: str) -> str:
            try:
                target = self._resolve(path)
            except ValueError as exc:
                return f"ERROR: {exc}"
            if not target.exists():
                return (
                    f"ERROR: {path!r} does not exist inside the connected folder "
                    f"({self._root}). Call list_folder on its parent directory to see "
                    "the real filename instead of guessing."
                )
            if not target.is_file():
                return f"ERROR: {path!r} is a directory, not a file -- use list_folder for it instead."
            try:
                data = target.read_bytes()[:MAX_FILE_BYTES]
            except OSError as exc:
                return f"ERROR: found {path!r} but could not read it: {exc}"
            return data.decode("utf-8", errors="replace")

        @self._mcp.tool(
            name="write_file",
            description=(
                "Write (create or overwrite) a text file in the connected host "
                "folder. `path` is relative to the folder root; parent "
                "directories are created if needed. The user sees the exact "
                "path and content and must approve *this specific write* -- "
                "expect rejection if it wasn't clearly what they asked for. "
                "Do not call this repeatedly hoping for a different answer."
            ),
        )
        async def write_file(path: str, content: str) -> str:
            try:
                target = self._resolve(path)
            except ValueError as exc:
                return f"ERROR: {exc}"
            if target.exists() and target.is_dir():
                return f"ERROR: {path!r} is a directory -- cannot write a file over it."
            if self._on_confirm_write is not None:
                approved = await self._on_confirm_write(str(target), content)
                if not approved:
                    return (
                        f"DENIED: the user did not approve writing to {target}. "
                        "Do not retry this same write -- ask the user what they'd "
                        "like to do instead."
                    )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            except OSError as exc:
                return f"ERROR: could not write {path!r}: {exc}"
            return f"Wrote {len(content)} chars to {target}."

    async def start(self) -> None:
        app = self._mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[self.host, f"{self.host}:{self.port}"],
                allowed_origins=[f"http://{self.host}:{self.port}"],
            ),
        )
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._task = asyncio.ensure_future(self._server.serve())

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._server = None
        self._task = None

    @property
    def mcp_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"
