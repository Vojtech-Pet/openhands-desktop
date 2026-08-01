"""Deterministic test of ConversationController._connect_and_sync's gap-
closing behavior: an event that arrives via the WebSocket *during* the REST
history fetch (the actual race window) must still reach the GUI exactly
once, not be dropped and not be duplicated.

Uses a stub client (not the real HTTP client) so the timing of the "REST
fetch" can be controlled precisely and deterministically -- the real race
window is normally a few milliseconds, too fast to reliably hit live.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication

from openhands_desktop.api.models import AppConversation
from openhands_desktop.core.conversation_controller import ConversationController

app = QApplication(sys.argv)


class StubClient:
    """Duck-typed stand-in for AppServerClient with a controllable delay
    before search_events() returns, so a websocket event can be injected
    into the exact race window this test targets.
    """

    def __init__(self) -> None:
        self.controller: ConversationController | None = None

    async def search_events(self, conversation_id: str, *, limit: int = 100):
        # Simulate REST latency. While this is "in flight", the test injects
        # a WebSocket event directly via the controller's _route_ws_event --
        # exactly the race window _connect_and_sync exists to close.
        await asyncio.sleep(0.2)
        return {
            "items": [
                {"id": "evt-1", "kind": "MessageEvent", "source": "user", "llm_message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}},
                {"id": "evt-2", "kind": "SystemPromptEvent", "source": "agent"},
            ]
        }


controller = None  # set below


async def main() -> None:
    global controller
    client = StubClient()
    controller = ConversationController(client)
    client.controller = controller

    received_batches: list[list] = []
    controller.events_received.connect(lambda events: received_batches.append(events))

    conversation = AppConversation(
        id="conv-test",
        title=None,
        llm_model=None,
        sandbox_status="RUNNING",
        execution_status=None,
        conversation_url=None,  # no real websocket connection attempted
        session_api_key=None,
        sandbox_id=None,
        raw={},
    )

    # Bypass the real WebSocket (no live sandbox here) -- directly exercise
    # the sync logic by calling the same sequence _connect_and_sync does,
    # but injecting the "during REST fetch" event manually instead of via a
    # real socket.
    controller._syncing = True
    controller._sync_buffer = []

    async def fetch_and_inject():
        fetch_task = asyncio.ensure_future(client.search_events(conversation.id))
        # Give the fetch a moment to actually start, then inject a live
        # event that arrives WHILE the REST call is still in flight --
        # exactly the historical gap.
        await asyncio.sleep(0.05)
        controller._route_ws_event({"id": "evt-live-during-gap", "kind": "ActionEvent", "tool_name": "glob", "source": "agent"})
        page = await fetch_task
        return page

    page = await fetch_and_inject()
    for raw_event in page.get("items", []):
        controller._batcher.ingest(raw_event)
    for raw_event in controller._sync_buffer:
        controller._batcher.ingest(raw_event)
    controller._batcher.flush()
    controller._sync_buffer = []
    controller._syncing = False

    all_events = [e for batch in received_batches for e in batch]
    ids = [e.event_id for e in all_events]
    print(f"received event ids: {ids}")

    assert "evt-live-during-gap" in ids, "event that arrived during the REST fetch was LOST"
    assert ids.count("evt-live-during-gap") == 1, "event was duplicated"
    assert "evt-1" in ids and "evt-2" in ids, "REST history events missing"
    print("PASS: event delivered during the REST fetch is present exactly once, alongside REST history")

    # Now the duplicate case: the REST response ALSO includes an event that
    # was already delivered live during the gap (id overlap) -- must not be
    # duplicated.
    controller2 = ConversationController(client)
    batches2: list[list] = []
    controller2.events_received.connect(lambda events: batches2.append(events))
    controller2._syncing = True
    controller2._sync_buffer = [{"id": "evt-dup", "kind": "ActionEvent", "tool_name": "grep", "source": "agent"}]
    rest_page_with_dup = {"items": [{"id": "evt-dup", "kind": "ActionEvent", "tool_name": "grep", "source": "agent"}]}
    for raw_event in rest_page_with_dup["items"]:
        controller2._batcher.ingest(raw_event)
    for raw_event in controller2._sync_buffer:
        controller2._batcher.ingest(raw_event)
    controller2._batcher.flush()

    all2 = [e for batch in batches2 for e in batch]
    ids2 = [e.event_id for e in all2]
    print(f"dup-case event ids: {ids2}")
    assert ids2.count("evt-dup") == 1, "overlapping event (in both REST and buffer) was duplicated"
    print("PASS: event present in both REST and buffer is delivered exactly once")


asyncio.run(main())
