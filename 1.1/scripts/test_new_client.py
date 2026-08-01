"""Standalone (no Qt) live test of the new client.py/models.py/
conversation_controller.py against a real running agent-server -- verifies
the new API layer actually works end-to-end before wiring the full GUI to
it. Not part of the shipped app.

Usage: uv run python3 scripts/test_new_client.py
Requires: agent-server-dev container running on localhost:8010 with
OH_SESSION_API_KEYS_0=dev-key-123 and an LLM configured.
"""

import asyncio
import sys

sys.path.insert(0, "src")

from openhands_desktop.api.client import AppServerClient  # noqa: E402


async def main() -> None:
    client = AppServerClient(base_url="http://127.0.0.1:8010", session_api_key="dev-key-123")
    try:
        assert await client.health(), "server not healthy"
        print("health: OK")

        conversation = await client.start_conversation(
            llm_model="openai/qwen3.6-35b-a3b-iq4_nl",
            llm_base_url="http://172.17.0.1:1234/v1",
            llm_api_key="lm-studio",
            initial_message_text="Say hello in exactly one short sentence, then call finish.",
        )
        print(f"created conversation: {conversation.id}, status={conversation.execution_status}")

        for _ in range(60):
            await asyncio.sleep(2)
            conversation = await client.get_conversation(conversation.id)
            print(f"  poll: status={conversation.execution_status}")
            if conversation.execution_status is not None and conversation.execution_status.value in (
                "finished",
                "error",
            ):
                break

        events = await client.search_events(conversation.id, limit=50)
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

        await client.delete_conversation(conversation.id)
        print("\ndeleted conversation -- test PASSED")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
