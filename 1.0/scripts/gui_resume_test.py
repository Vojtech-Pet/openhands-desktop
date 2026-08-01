"""Real end-to-end test of resuming a past conversation: create a real
conversation via the actual GUI flow, let it genuinely finish, then start a
FRESH conversation (simulating the user moving on), and finally click the
FIRST one's sidebar entry to verify it correctly reattaches -- log refills
with its real history and the state label reflects its real (already
finished) status, not a stale/wrong one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.core.completion import RunState
from openhands_desktop.ui.main_window import MainWindow

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

client = AppServerClient()
window = MainWindow(client)
window.show()

first_conversation_id: str | None = None
states_seen: list[RunState] = []
window._controller.state_changed.connect(lambda s: states_seen.append(s))


def send_first_task() -> None:
    window.input.setPlainText("Reply with exactly PONG and then call finish. Nothing else.")
    window._send()


async def wait_for_completion_then_start_second() -> None:
    global first_conversation_id
    for _ in range(60):
        await asyncio.sleep(2)
        if states_seen and states_seen[-1] in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED, RunState.ERROR):
            break
    first_conversation_id = window._controller.conversation_id
    print(f"first conversation finished: id={first_conversation_id} final_state={states_seen[-1] if states_seen else None}")
    assert states_seen and states_seen[-1] == RunState.COMPLETED, f"expected COMPLETED, got {states_seen}"

    # Move on: start a second, unrelated conversation (simulates the user
    # clicking "+" and doing something else afterward).
    window.sidebar.new_conversation_requested.emit()
    await asyncio.sleep(0.5)
    print("started fresh conversation (welcome screen); now resuming the FIRST one from history")

    # Now the actual test: click the first conversation's sidebar entry.
    states_seen.clear()
    window._resume_conversation(first_conversation_id)

    for _ in range(20):
        await asyncio.sleep(1)
        if states_seen:
            break
    print(f"states seen after resume: {states_seen}")
    print(f"log contents after resume:\n{window.log.toPlainText()}")
    assert window._controller.conversation_id == first_conversation_id
    assert states_seen and states_seen[-1] == RunState.COMPLETED, (
        f"resumed conversation should resolve as COMPLETED immediately, got {states_seen}"
    )
    assert "PONG" in window.log.toPlainText() or "pong" in window.log.toPlainText().lower(), (
        "resumed log should contain the real backfilled conversation content"
    )
    print("PASS: resumed conversation correctly reattached, backfilled history, and resolved COMPLETED")

    await client.delete_conversation(first_conversation_id)
    loop.stop()


def kick_off() -> None:
    send_first_task()
    asyncio.ensure_future(wait_for_completion_then_start_second())


QTimer.singleShot(1000, kick_off)

with loop:
    loop.run_forever()
