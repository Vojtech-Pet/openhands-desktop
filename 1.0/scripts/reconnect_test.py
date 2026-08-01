"""Live test of the reconnect path: force-close the underlying WebSocket
connection (simulating an unexpected network drop, NOT a deliberate
.stop()) and verify the controller notices, backs off, and reconnects on
its own -- without sending any stop/interrupt to the agent in the meantime.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.core.conversation_controller import ConversationController

app = QApplication(sys.argv)


async def main() -> None:
    client = AppServerClient()
    controller = ConversationController(client)

    errors: list[str] = []
    controller.error_occurred.connect(lambda msg: errors.append(msg))

    ready = asyncio.Event()
    controller.conversation_ready.connect(lambda _cid: ready.set())

    controller.start_new(
        llm_model="openai/lmstudio-qwen36-27b-q4",
        initial_message="Count slowly from 1 to 30, one number per line with a short reflection each. Do not call finish until 30.",
    )
    await asyncio.wait_for(ready.wait(), timeout=60)
    print(f"conversation ready: {controller.conversation_id}")

    await asyncio.sleep(3)  # let it settle into normal live streaming
    assert controller._ws is not None and controller._ws._conn is not None, "no active websocket connection to break"

    print("--- force-closing the underlying WebSocket connection (simulated drop) ---")
    old_ws = controller._ws
    await old_ws._conn.close()  # NOT .stop() -- this must look like an unexpected drop
    await asyncio.sleep(0.5)

    print(f"reconnecting flag right after drop: {controller._reconnecting}")
    assert controller._reconnecting, "controller did not notice the disconnect"

    print("waiting for automatic reconnect...")
    for i in range(20):
        await asyncio.sleep(1)
        if not controller._reconnecting and controller._ws is not None and controller._ws is not old_ws:
            print(f"reconnected after ~{i+1}s: new websocket client instance is active")
            break
    else:
        raise AssertionError("did not reconnect within the expected window")

    assert any("disconnected" in e.lower() for e in errors), "no disconnect notification was surfaced to the UI"
    print(f"errors surfaced during the test: {errors}")
    print("PASS: unexpected WebSocket drop was detected and automatically reconnected, "
          "without any explicit stop/interrupt being sent")

    await client.delete_conversation(controller.conversation_id)
    await controller.stop()
    await client.aclose()


asyncio.run(main())
