"""Live test: send a follow-up ("steering") message to a conversation while
its agent loop is still RUNNING, without stopping/restarting -- verifying
the actual queuing/steering capability the user asked about, not just
trusting the endpoint's docstring.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openhands_desktop.api.client import AppServerClient


async def main() -> None:
    client = AppServerClient()

    task = await client.start_conversation(
        llm_model="openai/lmstudio-qwen36-27b-q4",
        initial_message_text=(
            "Count slowly from 1 to 20. Write one number per line, with a short "
            "one-sentence reflection after each number about what that number "
            "reminds you of. Do not call finish until you reach 20."
        ),
    )
    while task.status.value not in ("READY", "ERROR"):
        await asyncio.sleep(1)
        task = await client.get_start_task(task.id)
    assert task.status.value == "READY", task.detail
    conv_id = task.app_conversation_id
    print(f"conversation: {conv_id}")

    # Give it a few seconds to actually start generating before we interrupt.
    await asyncio.sleep(8)
    conv = await client.get_conversation(conv_id)
    print(f"status before steering message: {conv.execution_status}")
    assert conv.execution_status and conv.execution_status.value == "running", (
        "expected the agent to already be mid-run before we send the steering message"
    )

    print(">>> sending steering message while agent is RUNNING (no stop/restart)")
    await client.send_message(conv_id, "Stop counting now. Just say DONE and call finish.")

    # Watch what happens next.
    for i in range(24):
        await asyncio.sleep(5)
        conv = await client.get_conversation(conv_id)
        print(f"  t+{(i+1)*5}s status={conv.execution_status}")
        if conv.execution_status and conv.execution_status.value in ("finished", "error"):
            break

    events = await client.search_events(conv_id, limit=100)
    print("\n--- MessageEvents (source=user and source=agent) after steering ---")
    for e in events["items"]:
        if e.get("kind") == "MessageEvent":
            msg = e.get("llm_message", {})
            text = "".join(
                c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text"
            )
            print(f"[{e.get('source')}] {text[:200]!r}")
        if e.get("kind") == "ActionEvent":
            print(f"[ActionEvent tool={e.get('tool_name')}]")

    await client.delete_conversation(conv_id)
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
