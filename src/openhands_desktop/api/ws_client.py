"""Async WebSocket client for the live event stream.

Connects directly to the conversation's sandbox agent-server (see
websocket_url.build_websocket_url) -- this is deliberately NOT the main
app-server API. The main app-server is only used for REST operations
(create/send-message/history). Reconnection is the caller's responsibility
(handled in ConversationController), matching how fragile a single sandbox
container's network path can be -- this class only reports that it
disconnected, once, per connection attempt; it does not retry itself.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import websockets
from websockets.asyncio.client import ClientConnection


class ConversationWebSocketClient:
    def __init__(
        self,
        url: str,
        on_event: Callable[[dict], None],
        on_error: Callable[[Exception], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
    ) -> None:
        self._url = url
        self._on_event = on_event
        self._on_error = on_error
        self._on_disconnected = on_disconnected
        self._task: asyncio.Task | None = None
        self._conn: ClientConnection | None = None
        self._stop = False
        self._connected = asyncio.Event()
        self._connection_error: Exception | None = None

    def start(self) -> None:
        self._stop = False
        self._connected.clear()
        self._connection_error = None
        self._task = asyncio.ensure_future(self._run())

    async def wait_connected(self, timeout: float = 10.0) -> None:
        """Wait until the socket handshake succeeds or fails."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        if self._connection_error is not None:
            raise self._connection_error
        if self._conn is None:
            raise ConnectionError("WebSocket closed before the handshake completed")

    async def stop(self) -> None:
        self._stop = True
        if self._conn is not None:
            await self._conn.close()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        try:
            async with websockets.connect(self._url, max_size=10_000_000) as conn:
                self._conn = conn
                self._connected.set()
                async for message in conn:
                    if self._stop:
                        break
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    self._on_event(data)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 -- surfaced to the caller, not swallowed
            self._connection_error = exc
            self._connected.set()
            if self._on_error is not None:
                self._on_error(exc)
        finally:
            self._connected.set()
            self._conn = None
            # Fires both on a clean server-side close (no exception raised --
            # the `async for` loop just ends) and after an exception, as long
            # as WE didn't request the stop. Either way the caller decides
            # whether to reconnect; this class never retries on its own.
            if not self._stop and self._on_disconnected is not None:
                self._on_disconnected()
