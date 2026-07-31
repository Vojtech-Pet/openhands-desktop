"""An MCP server hosted *inside* the desktop app, so the agent running in
its sandbox can ask the user a question with clickable options instead of
having to guess -- the equivalent of Claude Code's AskUserQuestion.

OpenHands has no native "ask the user" tool (confirmed 2026-07-29 by dumping
the agent's real tool list: terminal, editor, browser, task tracker,
subagent, finish, think, skills, git PR tools -- nothing interactive). MCP is
the supported extension point, and the sandbox can already reach the host
over the Docker bridge, which is exactly how the existing mempalace server
works.

Bound to the Docker bridge address rather than 0.0.0.0 on purpose: that
address is reachable from the host and its containers but not from the LAN,
so the tool cannot be driven by anything else on the network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

# Same bridge address the sandbox already uses to reach mempalace and
# LM Studio, so no new networking assumptions are introduced.
DEFAULT_HOST = "172.17.0.1"
DEFAULT_PORT = 8901


@dataclass
class PendingQuestion:
    question: str
    options: list[str]
    allow_free_text: bool
    multi_select: bool = False
    future: asyncio.Future = field(repr=False, default=None)  # type: ignore[assignment]


class AskUserServer:
    """Runs the MCP endpoint on the app's own asyncio loop.

    `on_question` is invoked on that same loop (qasync drives Qt from it),
    so it is safe for it to build widgets directly. It receives a
    PendingQuestion and must eventually resolve its `future` with the
    chosen answer -- or with an exception/cancel if the user dismisses it.
    """

    def __init__(
        self,
        on_question: Callable[[PendingQuestion], None],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._on_question = on_question
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

        self._mcp = MCPServer(
            name="openhands-desktop",
            instructions=(
                "Lets you ask the desktop user a question and get their answer. "
                "Use it when a decision is genuinely the user's to make and you "
                "cannot resolve it from the task or the code."
            ),
        )
        self._register_tools()

    def _register_tools(self) -> None:
        @self._mcp.tool(
            name="ask_user_question",
            description=(
                "Ask the desktop user a question and wait for their answer. "
                "Use when a decision is the user's to make (which approach to take, "
                "which file is authoritative, whether to proceed with something "
                "irreversible) -- not for things you can determine yourself. "
                "Provide 2-4 short options. Set multi_select=True if more than one "
                "option can apply at once (shown as checkboxes instead of buttons); "
                "the answer is then a comma-separated list of the checked options. "
                "Returns the option(s) the user picked, or their own typed answer."
            ),
        )
        async def ask_user_question(
            question: str, options: list[str], multi_select: bool = False
        ) -> str:
            if not question.strip():
                return "ERROR: question must not be empty."
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            pending = PendingQuestion(
                question=question.strip(),
                options=[o for o in (opt.strip() for opt in options) if o][:4],
                allow_free_text=True,
                multi_select=multi_select,
                future=future,
            )
            try:
                self._on_question(pending)
            except Exception as exc:  # noqa: BLE001 -- report instead of hanging the agent
                return f"ERROR: could not show the question in the desktop app: {exc}"
            try:
                return await future
            except asyncio.CancelledError:
                return "The user dismissed the question without answering."

    async def start(self) -> None:
        # DNS-rebinding protection stays ON; it only defaults to allowing
        # 127.0.0.1, which would reject the sandbox's requests to the bridge
        # address with "421 Misdirected Request" (confirmed live). Allowing
        # exactly the address being served is the narrow fix -- disabling the
        # protection outright would also accept arbitrary Host headers.
        app = self._mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[self.host, f"{self.host}:{self.port}"],
                allowed_origins=[f"http://{self.host}:{self.port}"],
            ),
        )
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
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
