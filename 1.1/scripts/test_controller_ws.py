"""Verifies ConversationController against the current OpenHands app-server,
including the WebSocket live-event path (test_new_client.py only covered
REST). Still no Qt GUI -- QObject/Signal work fine headless as long as
there's a Qt event loop driving them, which qasync provides here exactly
like the real app does.
"""

import asyncio
import sys

sys.path.insert(0, "src")

from PySide6.QtWidgets import QApplication  # noqa: E402
import qasync  # noqa: E402

from openhands_desktop.api.client import AppServerClient  # noqa: E402
from openhands_desktop.core.conversation_controller import ConversationController  # noqa: E402


async def main() -> None:
    client = AppServerClient(base_url="http://127.0.0.1:3000")
    controller = ConversationController(client)

    done = asyncio.Event()
    received_kinds: list[str] = []

    def on_events(events):
        for e in events:
            received_kinds.append(e.kind if hasattr(e, "kind") else str(type(e)))
        print(f"events_received: {[getattr(e, 'kind', e) for e in events]}")

    def on_state(state):
        print(f"state_changed: {state}")
        if str(state).endswith(("FINISHED", "ERROR", "COMPLETED")):
            done.set()

    def on_error(msg):
        print(f"error_occurred: {msg}")

    def on_ready(cid):
        print(f"conversation_ready: {cid}")

    controller.events_received.connect(on_events)
    controller.state_changed.connect(on_state)
    controller.error_occurred.connect(on_error)
    controller.conversation_ready.connect(on_ready)

    controller.start_new(
        llm_model="openai/qwen3.6-35b-a3b-iq4_nl",
        initial_message="Say hello in exactly one short sentence, then call finish.",
        agent_type="default",
    )

    try:
        await asyncio.wait_for(done.wait(), timeout=180)
        print("\nWS/controller test PASSED -- reached a terminal state")
    except asyncio.TimeoutError:
        print(f"\nWS/controller test: no terminal state in 180s. Events seen: {received_kinds}")
    finally:
        if controller.conversation_id:
            await client.delete_conversation(controller.conversation_id)
        await controller.stop()
        await client.aclose()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(main())
