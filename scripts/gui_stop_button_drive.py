"""Drives the real MainWindow to click the actual Stop button widget (not
just call controller.interrupt() from a script) -- verifying the button is
wired correctly end to end."""

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
        "Count slowly from 1 to 50. Write one number per line, with a short "
        "one-sentence reflection after each number. Do not call finish until "
        "you reach 50."
    )
    window._send()


def click_stop() -> None:
    print(f"--- clicking Stop button at state={window.state_label.text()} ---")
    window.stop_btn.click()


def report_and_quit() -> None:
    print("---FINAL STATE---")
    print(window.state_label.text())
    print("---FINAL LOG---")
    print(window.log.toPlainText())
    loop.stop()


QTimer.singleShot(1500, send_task)
QTimer.singleShot(25_000, click_stop)
QTimer.singleShot(35_000, report_and_quit)

with loop:
    loop.run_forever()
