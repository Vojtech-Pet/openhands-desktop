"""Headless smoke test of api/client.py + ws_client.py against a real running
OpenHands app-server. Not a unit test -- exercises the actual network path
end to end.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.websocket_url import build_websocket_url
from openhands_desktop.api.ws_client import ConversationWebSocketClient
from openhands_desktop.core.event_batcher import EventBatcher  # noqa: F401 -- needs QObject, not used here
from openhands_desktop.core.completion import CompletionTracker
from openhands_desktop.core.events import parse_event


async def main() -> None:
    client = AppServerClient()

    ok = await client.health()
    print(f"health: {ok}")
    assert ok, "server not healthy"

    task = await client.start_conversation(
        llm_model="openai/lmstudio-qwen36-27b-q4",
        initial_message_text="Reply with exactly the word PONG and then call finish. Do not do anything else.",
    )
    print(f"start task: {task.id} status={task.status}")

    while task.status.value not in ("READY", "ERROR"):
        await asyncio.sleep(1)
        task = await client.get_start_task(task.id)
        print(f"  poll: {task.status}")

    assert task.status.value == "READY", f"task failed: {task.detail}"
    conv_id = task.app_conversation_id
    print(f"conversation id: {conv_id}")

    conversation = await client.get_conversation(conv_id)
    print(f"conversation_url: {conversation.conversation_url}")
    print(f"session_api_key present: {conversation.session_api_key is not None}")

    ws_url = build_websocket_url(conversation.id, conversation.conversation_url, conversation.session_api_key)
    print(f"ws url: {ws_url}")

    completion = CompletionTracker()
    completion.reset_for_new_run()
    received = []

    def on_event(raw: dict) -> None:
        event = parse_event(raw)
        completion.observe_event(event)
        received.append(event)
        if event.kind.value != "StreamingDeltaEvent":
            print(f"  event: kind={event.raw_kind} tool={event.tool_name} text={(event.text or '')[:80]!r}")

    ws = ConversationWebSocketClient(ws_url, on_event=on_event, on_error=lambda e: print(f"  ws error: {e}"))
    ws.start()

    finished = False
    for _ in range(60):
        await asyncio.sleep(2)
        conversation = await client.get_conversation(conv_id)
        state = completion.resolve(conversation.execution_status)
        print(f"  execution_status={conversation.execution_status} resolved_state={state}")
        if conversation.execution_status and conversation.execution_status.value in ("finished", "error", "stuck"):
            finished = True
            break

    await ws.stop()
    print(f"total events received: {len(received)}")
    print(f"finish() action observed: {completion._finish_action_seen}")
    assert finished, "conversation never reached a terminal status"

    await client.delete_conversation(conv_id)
    print("conversation deleted, cleanup done")
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
