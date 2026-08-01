"""Live test of SandboxConversationClient.interrupt() -- verifying the hard
stop actually cancels an in-flight LLM call quickly, not just accepts a
follow-up message for later (that was already confirmed separately in
steering_test.py).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.sandbox_client import SandboxConversationClient


async def main() -> None:
    client = AppServerClient()

    task = await client.start_conversation(
        llm_model="openai/lmstudio-qwen36-27b-q4",
        initial_message_text=(
            "Count slowly from 1 to 50. Write one number per line, with a short "
            "one-sentence reflection after each number. Do not call finish until "
            "you reach 50."
        ),
    )
    while task.status.value not in ("READY", "ERROR"):
        await asyncio.sleep(1)
        task = await client.get_start_task(task.id)
    assert task.status.value == "READY", task.detail
    conv_id = task.app_conversation_id
    print(f"conversation: {conv_id}")

    # Wait until it's genuinely mid-generation.
    await asyncio.sleep(8)
    conv = await client.get_conversation(conv_id)
    print(f"status before interrupt: {conv.execution_status}")
    assert conv.execution_status and conv.execution_status.value == "running"

    sandbox = SandboxConversationClient(conv.conversation_url, conv.session_api_key)
    t0 = time.monotonic()
    ok = await sandbox.interrupt()
    print(f"interrupt() call returned ok={ok} after {time.monotonic() - t0:.2f}s")

    # Poll quickly (short interval) to see how fast the status actually changes.
    for i in range(20):
        await asyncio.sleep(1)
        conv = await client.get_conversation(conv_id)
        elapsed = time.monotonic() - t0
        print(f"  t+{elapsed:.1f}s status={conv.execution_status}")
        if conv.execution_status and conv.execution_status.value not in ("running",):
            break

    await sandbox.aclose()
    await client.delete_conversation(conv_id)
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
