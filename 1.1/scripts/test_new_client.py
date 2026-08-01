"""Standalone (no Qt) live test of client.py/models.py against the current
OpenHands app-server on port 3000. Not part of the shipped app.

Usage: uv run python3 scripts/test_new_client.py
Requires: OpenHands backend running on localhost:3000 and an LLM profile
configured.
"""

import asyncio
import sys

sys.path.insert(0, "src")

from openhands_desktop.api.client import AppServerClient  # noqa: E402


async def main() -> None:
    client = AppServerClient(base_url="http://127.0.0.1:3000")
    try:
        assert await client.health(), "server not healthy"
        print("health: OK")

        task = await client.start_conversation(
            llm_model="openai/qwen3.6-35b-a3b-iq4_nl",
            initial_message_text="Say hello in exactly one short sentence, then call finish.",
        )
        print(f"start task: {task.id}, status={task.status}")

        for _ in range(120):
            await asyncio.sleep(1)
            task = await client.get_start_task(task.id)
            print(f"  start poll: status={task.status}")
            if task.status.value in ("READY", "ERROR"):
                break
        assert task.status.value == "READY" and task.app_conversation_id, task.detail or "start failed"

        for _ in range(60):
            await asyncio.sleep(2)
            conversation = await client.get_conversation(task.app_conversation_id)
            print(f"  poll: status={conversation.execution_status}")
            if conversation.execution_status is not None and conversation.execution_status.value in (
                "finished",
                "error",
            ):
                break

        events = await client.search_events(task.app_conversation_id, limit=50)
        print(f"\n{len(events.get('items', []))} events:")
        for ev in events.get("items", []):
            kind = ev.get("kind")
            source = ev.get("source")
            text = ""
            msg = ev.get("llm_message") or {}
            for block in msg.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    text = block["text"][:200]
            print(f"  [{source}] {kind}: {text}")

        await client.delete_conversation(task.app_conversation_id)
        print("\ndeleted conversation -- test PASSED")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
