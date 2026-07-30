"""Quick live check that the new WS-first sync flow (_connect_and_sync)
works end to end against a real server without crashing and still delivers
both the backfilled history and live events through the real MainWindow."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.ui.main_window import MainWindow

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

client = AppServerClient()
window = MainWindow(client)
window.show()


def send_task() -> None:
    window.input.setText("Reply with exactly PONG and call finish. Nothing else.")
    window._send()


def report_and_quit() -> None:
    print("---LOG---")
    print(window.log.toPlainText())
    print("---conversation_id---", window._controller.conversation_id)
    loop.stop()


QTimer.singleShot(1000, send_task)
QTimer.singleShot(20_000, report_and_quit)

with loop:
    loop.run_forever()
