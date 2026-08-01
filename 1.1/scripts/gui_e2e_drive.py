"""Drives the real MainWindow programmatically (no OS-level input simulation
available on this Wayland session) to exercise the actual GUI code path --
ConversationController, EventBatcher, MainWindow._render_event -- end to end
against the live server, not just the headless API smoke test.
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
from openhands_desktop.ui.main_window import MainWindow

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

client = AppServerClient()
window = MainWindow(client)
window.show()


def send_task() -> None:
    window.input.setText(
        "Reply with exactly the word PONG and then call finish. Do not do anything else."
    )
    window._send()


def quit_app() -> None:
    print("---FINAL LOG---")
    print(window.log.toPlainText())
    loop.stop()


QTimer.singleShot(1500, send_task)
QTimer.singleShot(180_000, quit_app)

with loop:
    loop.run_forever()
