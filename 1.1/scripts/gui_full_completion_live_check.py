"""Real end-to-end check that a simple PONG+finish task still correctly
reaches RunState.COMPLETED (green) through the real GUI after today's two
changes (WS-first sync + timestamp-scoped CompletionTracker)."""

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

final_states: list[RunState] = []
window._controller.state_changed.connect(lambda s: final_states.append(s))


def send_task() -> None:
    window.input.setText("Reply with exactly PONG and then call finish. Nothing else.")
    window._send()


async def wait_for_completion() -> None:
    for _ in range(60):
        await asyncio.sleep(2)
        if final_states and final_states[-1] in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED, RunState.ERROR):
            break
    print(f"state history: {final_states}")
    print(f"final label: {window.state_label.text()!r}")
    conv_id = window._controller.conversation_id
    print(f"conversation_id: {conv_id}")
    assert final_states and final_states[-1] == RunState.COMPLETED, (
        f"expected COMPLETED, got {final_states[-1] if final_states else None}"
    )
    print("PASS: real live run correctly reached COMPLETED with the new sync+completion logic")
    if conv_id:
        await client.delete_conversation(conv_id)
    loop.stop()


def kick_off() -> None:
    send_task()
    asyncio.ensure_future(wait_for_completion())


QTimer.singleShot(1000, kick_off)

with loop:
    loop.run_forever()
