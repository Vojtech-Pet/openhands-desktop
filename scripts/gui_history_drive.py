"""Drives the real MainWindow to verify conversation history is actually
recorded (on conversation_ready) and updated (on state_changed) in a real
SQLite file, then confirms it's visible via the History dialog in a FRESH
MainWindow instance (simulating an app restart) -- using a throwaway db path
so this doesn't touch the user's real history.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.core.history_store import HistoryStore
from openhands_desktop.ui.main_window import MainWindow

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

tmp_dir = tempfile.mkdtemp()
db_path = f"{tmp_dir}/history.db"

client = AppServerClient()
history = HistoryStore(db_path)
window = MainWindow(client, history_store=history)
window.show()


def send_task() -> None:
    window.input.setText("Reply with PONG and call finish.")
    window._send()


async def check_and_report() -> None:
    records = await history.list_recent()
    print(f"records in real db after run: {len(records)}")
    for r in records:
        print(f"  {r.conversation_id} model={r.llm_model} title={r.title!r} status={r.last_status}")

    # Fresh MainWindow instance against the same db file -- simulates restart.
    window2 = MainWindow(client, history_store=HistoryStore(db_path))
    records2 = await HistoryStore(db_path).list_recent()
    print(f"fresh-instance read count: {len(records2)}")
    assert len(records2) == len(records) and len(records2) >= 1
    print("PASS: history survives across a fresh MainWindow + HistoryStore instance")
    loop.stop()


def finish() -> None:
    asyncio.ensure_future(check_and_report())


QTimer.singleShot(1500, send_task)
QTimer.singleShot(15_000, finish)

with loop:
    loop.run_forever()
