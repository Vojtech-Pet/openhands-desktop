"""Owns one conversation's lifecycle: create -> poll start-task -> connect
WebSocket -> stream events -> track completion. All async work runs on the
qasync-integrated Qt event loop, so slots can call `asyncio.ensure_future`
directly instead of spinning up a separate thread.
"""

from __future__ import annotations

import asyncio
import random

from PySide6.QtCore import QObject, Signal

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.models import AppConversation, ExecutionStatus
from openhands_desktop.api.sandbox_client import SandboxConversationClient
from openhands_desktop.api.url_validation import validate_conversation_url
from openhands_desktop.api.websocket_url import build_websocket_url
from openhands_desktop.api.ws_client import ConversationWebSocketClient
from openhands_desktop.core.completion import CompletionTracker, RunState
from openhands_desktop.core.event_batcher import EventBatcher
from openhands_desktop.core.events import NormalizedEvent

START_TASK_POLL_INTERVAL_S = 1.0
STATUS_POLL_INTERVAL_S = 2.0
RECONNECT_DELAYS_S = [0.5, 1.0, 2.0, 4.0, 8.0, 15.0]  # last value repeats thereafter


class ConversationController(QObject):
    events_received = Signal(list)  # list[NormalizedEvent]
    state_changed = Signal(object)  # RunState
    error_occurred = Signal(str)
    conversation_ready = Signal(str)  # conversation_id
    starting_changed = Signal(bool)
    compact_finished = Signal(bool, str)
    # (used_tokens, context_window) straight from the server's own metrics --
    # see _poll_status for why per_turn_token is the right "how full is the
    # context" field rather than the accumulated prompt_tokens.
    token_usage_changed = Signal(int, int)

    def __init__(self, client: AppServerClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._batcher = EventBatcher(parent=self)
        self._batcher.events_ready.connect(self._on_batch)
        self._completion = CompletionTracker()
        self._ws: ConversationWebSocketClient | None = None
        self._sandbox: SandboxConversationClient | None = None
        self.conversation_id: str | None = None
        self.llm_model: str | None = None
        self.sub_conversation_ids: list[str] = []
        self._status_poll_task: asyncio.Task | None = None
        self._lifecycle_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._stopping = False
        # While True, incoming WebSocket events are buffered instead of fed
        # to the batcher -- see _start_new's connect-then-backfill ordering.
        self._syncing = False
        self._sync_buffer: list[dict] = []
        self._reconnecting = False

    def start_new(
        self,
        *,
        llm_model: str | None = None,
        initial_message: str | None = None,
        agent_type: str = "default",
        parent_conversation_id: str | None = None,
        system_message_suffix: str | None = None,
    ) -> None:
        if self._lifecycle_task is not None and not self._lifecycle_task.done():
            self.error_occurred.emit("A conversation is already being started")
            return
        self._stopping = False
        self.starting_changed.emit(True)
        self._lifecycle_task = self._spawn(
            self._start_new(
                llm_model=llm_model,
                initial_message=initial_message,
                agent_type=agent_type,
                parent_conversation_id=parent_conversation_id,
                system_message_suffix=system_message_suffix,
            ),
            on_done=lambda: self.starting_changed.emit(False),
        )

    async def _start_new(
        self,
        *,
        llm_model: str | None,
        initial_message: str | None,
        agent_type: str = "default",
        parent_conversation_id: str | None = None,
        system_message_suffix: str | None = None,
    ) -> None:
        try:
            # Reset before the conversation even exists, not right before the
            # backfill: the sandbox can start processing the initial message
            # during the READY-polling wait below, and the completion
            # boundary must be safely before that, not just before the
            # backfill call (see completion.py's docstring on stale finish()
            # detection for why the boundary's exact placement matters).
            self._completion.reset_for_new_run()
            task = await self._client.start_conversation(
                llm_model=llm_model,
                initial_message_text=initial_message,
                agent_type=agent_type,
                parent_conversation_id=parent_conversation_id,
                system_message_suffix=system_message_suffix,
            )
            while task.status.value not in ("READY", "ERROR"):
                await asyncio.sleep(START_TASK_POLL_INTERVAL_S)
                task = await self._client.get_start_task(task.id)

            if task.status.value == "ERROR" or task.app_conversation_id is None:
                self.error_occurred.emit(task.detail or "Conversation failed to start")
                return

            self.conversation_id = task.app_conversation_id
            conversation = await self._client.get_conversation(self.conversation_id)
            await self._connect_and_sync(conversation)
            self.conversation_ready.emit(self.conversation_id)
            self._start_status_polling()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(str(exc))

    def attach(self, conversation_id: str) -> None:
        """Reattaches to an *existing* conversation (e.g. picked from the
        sidebar's history) instead of creating a new one -- no start-task
        polling needed since the sandbox already exists."""
        if self._lifecycle_task is not None and not self._lifecycle_task.done():
            self.error_occurred.emit("Another conversation is still being opened")
            return
        self._stopping = False
        self.starting_changed.emit(True)
        self._lifecycle_task = self._spawn(
            self._attach(conversation_id),
            on_done=lambda: self.starting_changed.emit(False),
        )

    async def _attach(self, conversation_id: str) -> None:
        try:
            self._completion.reset_for_attach()
            self.conversation_id = conversation_id
            conversation = await self._client.get_conversation(conversation_id)
            await self._connect_and_sync(conversation)
            self.conversation_ready.emit(self.conversation_id)
            self._start_status_polling()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Could not reattach to conversation: {exc}")

    async def _connect_and_sync(self, conversation: AppConversation) -> None:
        """Connects the WebSocket *before* fetching REST history, buffering
        any events that arrive in between, then merges REST history with the
        buffer (relying on EventBatcher's own id-based dedup) before
        switching to live delivery.

        Why this order and not REST-then-connect: the socket can only be
        opened once `conversation_url` is known, by which point the sandbox
        may have already produced events (SystemPromptEvent, an initial
        MessageEvent, etc. -- see the docstring this replaced). Fetching
        REST history *first* and connecting *after* leaves a real gap: any
        event produced between the REST response and the socket handshake
        completing is never seen by either path. Connecting first and
        buffering closes that gap -- the REST fetch and the buffer together
        cover the whole timeline, and duplicates between the two are merged
        away by id, not silently dropped or duplicated.
        """
        self._syncing = True
        self._sync_buffer = []
        self.llm_model = conversation.llm_model
        self.sub_conversation_ids = list(conversation.raw.get("sub_conversation_ids") or [])
        try:
            if self._ws is not None:
                await self._ws.stop()
                self._ws = None
            if self._sandbox is not None:
                await self._sandbox.aclose()
                self._sandbox = None
            if not self._connect_websocket(conversation):
                raise RuntimeError("conversation_url failed validation, refusing to connect")

            assert self._ws is not None
            await self._ws.wait_connected()

            page = await self._client.search_events(conversation.id, limit=100)
            rest_events = page.get("items", [])

            for raw_event in rest_events:
                self._batcher.ingest(raw_event)
            for raw_event in self._sync_buffer:
                self._batcher.ingest(raw_event)
            self._batcher.flush()
        finally:
            self._sync_buffer = []
            self._syncing = False

    def _route_ws_event(self, raw_event: dict) -> None:
        if self._syncing:
            self._sync_buffer.append(raw_event)
        else:
            self._batcher.ingest(raw_event)

    def send_message(self, text: str) -> None:
        if self.conversation_id is None:
            self.error_occurred.emit("No active conversation")
            return
        self._completion.reset_for_new_run()
        self._spawn(self._send_message(text))

    async def _send_message(self, text: str) -> None:
        assert self.conversation_id is not None
        try:
            await self._client.send_message(self.conversation_id, text)
        except Exception as exc:  # noqa: BLE001
            error_msg = self._format_error_message(exc)
            self.error_occurred.emit(error_msg)

    @staticmethod
    def _format_error_message(exc: Exception) -> str:
        """Format exception into user-friendly error message."""
        import httpx
        import json

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            try:
                error_data = exc.response.json()
                if isinstance(error_data, dict):
                    error_type = error_data.get("error", "UNKNOWN_ERROR")
                    message = error_data.get("message", "Unknown error")
                    details = error_data.get("details", "")
                    next_steps = error_data.get("next_steps", [])

                    # Build formatted message
                    msg = f"❌ {error_type}\n{message}"
                    if details:
                        msg += f"\n\nDetails: {details}"
                    if next_steps:
                        msg += "\n\nNext steps:"
                        for step in next_steps:
                            msg += f"\n  • {step}"
                    return msg
            except (json.JSONDecodeError, ValueError):
                pass

            # Fallback for non-JSON errors
            if status == 504:
                return "❌ AGENT_TIMEOUT\nAgent stopped responding. Check logs and try again."
            elif status == 502:
                return "❌ AGENT_ERROR\nAgent server error. Check if agent is running."
            else:
                return f"❌ HTTP {status}\n{exc.response.text[:200]}"

        # Generic error
        return f"❌ Error\n{str(exc)}"

    def _connect_websocket(self, conversation: AppConversation) -> bool:
        validation = validate_conversation_url(
            conversation.conversation_url, app_server_base_url=self._client.base_url
        )
        if not validation.ok:
            self.error_occurred.emit(f"Refusing to connect: {validation.reason}")
            return False
        if not conversation.session_api_key:
            self.error_occurred.emit("Refusing to connect: session_api_key is empty")
            return False

        url = build_websocket_url(conversation.id, conversation.conversation_url, conversation.session_api_key)
        self._ws = ConversationWebSocketClient(
            url,
            on_event=self._route_ws_event,
            on_error=lambda exc: self.error_occurred.emit(f"WebSocket error: {exc}"),
            on_disconnected=self._on_ws_disconnected,
        )
        self._ws.start()
        self._sandbox = SandboxConversationClient(
            conversation.conversation_url, conversation.session_api_key
        )
        return True

    def _on_ws_disconnected(self) -> None:
        if self._stopping or self._reconnecting or self.conversation_id is None:
            return
        self.error_occurred.emit("WebSocket disconnected -- attempting to reconnect")
        self._reconnect_task = self._spawn(self._reconnect())

    async def _reconnect(self) -> None:
        """Reconnects after an unexpected WebSocket drop, without touching
        the agent itself (no implicit pause/interrupt -- the run may still
        be progressing server-side while the client is merely disconnected).
        Reuses _connect_and_sync so the same buffer-then-merge logic that
        closes the initial-connect race also covers whatever happened during
        the outage.
        """
        self._reconnecting = True
        attempt = 0
        try:
            while self.conversation_id is not None:
                delay = RECONNECT_DELAYS_S[min(attempt, len(RECONNECT_DELAYS_S) - 1)]
                delay += random.uniform(0, delay * 0.2)  # jitter, so multiple
                await asyncio.sleep(delay)               # conversations don't
                attempt += 1                              # retry in lockstep
                try:
                    conversation = await self._client.get_conversation(self.conversation_id)
                    await self._connect_and_sync(conversation)
                except Exception:  # noqa: BLE001 -- app-server itself may be down; keep retrying
                    continue
                return
        finally:
            self._reconnecting = False

    def interrupt(self) -> None:
        """Hard stop: cancels the in-flight LLM request immediately (as
        opposed to a follow-up message, which is only picked up at the next
        turn boundary -- see POST {conversation_url}/interrupt, verified live
        to take effect in ~1s, vs. /pause which waits for the current call to
        finish).
        """
        if self._sandbox is None:
            self.error_occurred.emit("No active conversation to interrupt")
            return
        self._spawn(self._interrupt())

    async def _interrupt(self) -> None:
        assert self._sandbox is not None
        try:
            await self._sandbox.interrupt()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Could not interrupt the agent: {exc}")

    def condense(self) -> None:
        """Forces the conversation's history to be condensed right now (the
        equivalent of Claude Code's /compact), instead of waiting for the
        automatic condenser threshold."""
        if self._sandbox is None:
            self.error_occurred.emit("No active conversation to condense")
            return
        self._spawn(self._condense())

    async def _condense(self) -> None:
        assert self._sandbox is not None
        try:
            await self._sandbox.condense()
        except Exception as exc:  # noqa: BLE001
            self.compact_finished.emit(False, str(exc))

    def switch_llm(self, llm_config: dict) -> None:
        """Applies an LLM config change (e.g. a chat_template_kwargs toggle)
        to this conversation immediately instead of only the next one."""
        if self._sandbox is None:
            return
        self._spawn(self._switch_llm(llm_config))

    async def _switch_llm(self, llm_config: dict) -> None:
        assert self._sandbox is not None
        try:
            await self._sandbox.switch_llm(llm_config)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Could not apply the setting to the live conversation: {exc}")
            return
        self.compact_finished.emit(True, "History condensation completed")

    def _on_batch(self, events: list[NormalizedEvent]) -> None:
        for event in events:
            self._completion.observe_event(event)
        self.events_received.emit(events)

    def _start_status_polling(self) -> None:
        if self._status_poll_task is not None:
            self._status_poll_task.cancel()
        self._status_poll_task = asyncio.ensure_future(self._poll_status())

    # Confirmed live 2026-08-02: under heavy host load (local LLM +
    # subagent work running at once) app_server's own connection handling
    # briefly flapped -- fail, succeed, fail, succeed -- for several
    # seconds. The old one-failure gate re-armed on every intervening
    # success, so each flap produced its own red "Status check failed"
    # card (4 in a row for one ~8s blip) even though nothing was actually,
    # durably down. Requiring a few *consecutive* failures before
    # surfacing anything filters that noise while still catching a real
    # sustained outage within ~6s.
    _STATUS_POLL_FAILURE_THRESHOLD = 3

    async def _poll_status(self) -> None:
        assert self.conversation_id is not None
        last_state: RunState | None = None
        try:
            reported_error = False
            consecutive_failures = 0
            while not self._stopping and self.conversation_id is not None:
                try:
                    conversation = await self._client.get_conversation(self.conversation_id)
                    reported_error = False
                    consecutive_failures = 0
                except Exception as exc:  # noqa: BLE001
                    consecutive_failures += 1
                    if (
                        consecutive_failures >= self._STATUS_POLL_FAILURE_THRESHOLD
                        and not reported_error
                    ):
                        self.error_occurred.emit(f"Status check failed: {exc}")
                        reported_error = True
                    await asyncio.sleep(STATUS_POLL_INTERVAL_S)
                    continue
                self.sub_conversation_ids = list(conversation.raw.get("sub_conversation_ids") or [])
                state = self._completion.resolve(conversation.execution_status)
                if state != last_state:
                    last_state = state
                    self.state_changed.emit(state)
                self._emit_token_usage(conversation)
                await asyncio.sleep(STATUS_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            pass

    def _emit_token_usage(self, conversation: AppConversation) -> None:
        """Extracts real token usage from the conversation's own metrics.

        Field semantics confirmed live 2026-07-29 by watching them evolve
        across a multi-tool-call run:
        - `prompt_tokens` accumulates across every LLM call (310 -> 18613 ->
          55557 -> 74193 in one short run), so it is NOT context occupancy.
        - `per_turn_token` is the size of the most recent request (18393 ->
          18589 -> 18689), i.e. how full the context actually is right now.
        - `context_window` mirrors the profile's max_input_tokens.

        Emits nothing when the server reports zeros -- that happens before
        the first LLM call, and MainWindow keeps its own estimate as the
        fallback for that window.
        """
        usage = (conversation.raw.get("metrics") or {}).get("accumulated_token_usage") or {}
        used = usage.get("per_turn_token") or 0
        window = usage.get("context_window") or 0
        if used > 0 and window > 0:
            self.token_usage_changed.emit(int(used), int(window))

    async def stop(self) -> None:
        self._stopping = True
        self.conversation_id = None
        current = asyncio.current_task()
        tasks = [
            task
            for task in (self._lifecycle_task, self._reconnect_task, self._status_poll_task)
            if task is not None and task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._status_poll_task is not None:
            self._status_poll_task.cancel()
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        if self._sandbox is not None:
            await self._sandbox.aclose()
            self._sandbox = None
        pending = [task for task in self._background_tasks if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _spawn(self, coro, *, on_done=None) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)

        def _finished(done: asyncio.Task) -> None:
            self._background_tasks.discard(done)
            if on_done is not None:
                on_done()
            if done.cancelled():
                return
            try:
                done.result()
            except Exception as exc:  # noqa: BLE001
                self.error_occurred.emit(str(exc))

        task.add_done_callback(_finished)
        return task
