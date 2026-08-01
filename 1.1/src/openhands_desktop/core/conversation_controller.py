"""Owns one conversation's lifecycle against the new Agent Server: create
(synchronous, no start-task polling) -> connect WebSocket -> stream events
-> track completion. All async work runs on the qasync-integrated Qt event
loop, so slots can call `asyncio.ensure_future` directly instead of
spinning up a separate thread.

Simpler than the old app-server-era controller in one real way: there is no
more per-conversation SandboxConversationClient (a second HTTP client
talking to that conversation's own sandbox container at its own
conversation_url/session_api_key) -- pause/interrupt/condense/switch_llm
are now just more methods on the same single AppServerClient every other
call already goes through, since every conversation lives on the same
Agent Server.
"""

from __future__ import annotations

import asyncio
import random

from PySide6.QtCore import QObject, Signal

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.models import AppConversation, ExecutionStatus
from openhands_desktop.api.websocket_url import build_websocket_url
from openhands_desktop.api.ws_client import ConversationWebSocketClient
from openhands_desktop.core.completion import CompletionTracker, RunState
from openhands_desktop.core.event_batcher import EventBatcher
from openhands_desktop.core.events import NormalizedEvent

STATUS_POLL_INTERVAL_S = 2.0
RECONNECT_DELAYS_S = [0.5, 1.0, 2.0, 4.0, 8.0, 15.0]  # last value repeats thereafter


class ConversationController(QObject):
    events_received = Signal(list)  # list[NormalizedEvent]
    state_changed = Signal(object)  # RunState
    error_occurred = Signal(str)
    conversation_ready = Signal(str)  # conversation_id
    starting_changed = Signal(bool)
    compact_finished = Signal(bool, str)
    token_usage_changed = Signal(int, int)

    def __init__(self, client: AppServerClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._batcher = EventBatcher(parent=self)
        self._batcher.events_ready.connect(self._on_batch)
        self._completion = CompletionTracker()
        self._ws: ConversationWebSocketClient | None = None
        self.conversation_id: str | None = None
        self.llm_model: str | None = None
        self.sub_conversation_ids: list[str] = []
        self._status_poll_task: asyncio.Task | None = None
        self._lifecycle_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._stopping = False
        self._syncing = False
        self._sync_buffer: list[dict] = []
        self._reconnecting = False

    def start_new(
        self,
        *,
        llm_model: str,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        initial_message: str | None = None,
        system_message_suffix: str | None = None,
        mcp_config: dict | None = None,
    ) -> None:
        if self._lifecycle_task is not None and not self._lifecycle_task.done():
            self.error_occurred.emit("A conversation is already being started")
            return
        self._stopping = False
        self.starting_changed.emit(True)
        self._lifecycle_task = self._spawn(
            self._start_new(
                llm_model=llm_model,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                initial_message=initial_message,
                system_message_suffix=system_message_suffix,
                mcp_config=mcp_config,
            ),
            on_done=lambda: self.starting_changed.emit(False),
        )

    async def _start_new(
        self,
        *,
        llm_model: str,
        llm_base_url: str | None,
        llm_api_key: str | None,
        initial_message: str | None,
        system_message_suffix: str | None,
        mcp_config: dict | None = None,
    ) -> None:
        try:
            self._completion.reset_for_new_run()
            conversation = await self._client.start_conversation(
                llm_model=llm_model,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                initial_message_text=initial_message,
                system_message_suffix=system_message_suffix,
                mcp_config=mcp_config,
            )
            self.conversation_id = conversation.id
            await self._connect_and_sync(conversation)
            self.conversation_ready.emit(self.conversation_id)
            self._start_status_polling()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(str(exc))

    def attach(self, conversation_id: str) -> None:
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
        switching to live delivery. Same ordering rationale as the old
        app-server controller -- only the connection target changed (one
        shared server's base_url instead of a per-conversation
        conversation_url)."""
        self._syncing = True
        self._sync_buffer = []
        self.llm_model = conversation.llm_model
        self.sub_conversation_ids = list(conversation.raw.get("sub_conversation_ids") or [])
        try:
            if self._ws is not None:
                await self._ws.stop()
                self._ws = None
            self._connect_websocket(conversation.id)

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
            self.error_occurred.emit(self._format_error_message(exc))

    @staticmethod
    def _format_error_message(exc: Exception) -> str:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            try:
                error_data = exc.response.json()
                if isinstance(error_data, dict) and "detail" in error_data:
                    return f"❌ HTTP {status}\n{error_data['detail']}"
            except (ValueError, KeyError):
                pass
            if status == 504:
                return "❌ AGENT_TIMEOUT\nAgent stopped responding. Check logs and try again."
            elif status == 502:
                return "❌ AGENT_ERROR\nAgent server error. Check if agent is running."
            return f"❌ HTTP {status}\n{exc.response.text[:200]}"
        return f"❌ Error\n{str(exc)}"

    def _connect_websocket(self, conversation_id: str) -> None:
        url = build_websocket_url(conversation_id, self._client.base_url, self._client.session_api_key)
        self._ws = ConversationWebSocketClient(
            url,
            on_event=self._route_ws_event,
            on_error=lambda exc: self.error_occurred.emit(f"WebSocket error: {exc}"),
            on_disconnected=self._on_ws_disconnected,
        )
        self._ws.start()

    def _on_ws_disconnected(self) -> None:
        if self._stopping or self._reconnecting or self.conversation_id is None:
            return
        self.error_occurred.emit("WebSocket disconnected -- attempting to reconnect")
        self._reconnect_task = self._spawn(self._reconnect())

    async def _reconnect(self) -> None:
        self._reconnecting = True
        attempt = 0
        try:
            while self.conversation_id is not None:
                delay = RECONNECT_DELAYS_S[min(attempt, len(RECONNECT_DELAYS_S) - 1)]
                delay += random.uniform(0, delay * 0.2)
                await asyncio.sleep(delay)
                attempt += 1
                try:
                    conversation = await self._client.get_conversation(self.conversation_id)
                    await self._connect_and_sync(conversation)
                except Exception:  # noqa: BLE001 -- server itself may be down; keep retrying
                    continue
                return
        finally:
            self._reconnecting = False

    def interrupt(self) -> None:
        if self.conversation_id is None:
            self.error_occurred.emit("No active conversation to interrupt")
            return
        self._spawn(self._interrupt())

    async def _interrupt(self) -> None:
        assert self.conversation_id is not None
        try:
            await self._client.interrupt(self.conversation_id)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Could not interrupt the agent: {exc}")

    def condense(self) -> None:
        if self.conversation_id is None:
            self.error_occurred.emit("No active conversation to condense")
            return
        self._spawn(self._condense())

    async def _condense(self) -> None:
        assert self.conversation_id is not None
        try:
            await self._client.condense(self.conversation_id)
        except Exception as exc:  # noqa: BLE001
            self.compact_finished.emit(False, str(exc))

    def switch_llm(self, llm_config: dict) -> None:
        if self.conversation_id is None:
            return
        self._spawn(self._switch_llm(llm_config))

    async def _switch_llm(self, llm_config: dict) -> None:
        assert self.conversation_id is not None
        try:
            await self._client.switch_llm(self.conversation_id, llm_config)
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

    async def _poll_status(self) -> None:
        assert self.conversation_id is not None
        last_state: RunState | None = None
        try:
            reported_error = False
            while not self._stopping and self.conversation_id is not None:
                try:
                    conversation = await self._client.get_conversation(self.conversation_id)
                    reported_error = False
                except Exception as exc:  # noqa: BLE001
                    if not reported_error:
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
        usage = (conversation.raw.get("stats") or {}).get("usage_to_metrics", {}).get("agent", {}).get(
            "accumulated_token_usage"
        ) or {}
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
