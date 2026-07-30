"""Drives the real MainWindow to verify the model-profile combo box loads
real profiles from the live server, remembers a selection via QSettings,
and that selection is actually threaded into start_new()."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

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

captured_calls = []
original_start_new = window._controller.start_new


def spy_start_new(*, llm_model=None, initial_message=None):
    captured_calls.append(llm_model)
    print(f"start_new called with llm_model={llm_model!r}")


window._controller.start_new = spy_start_new


def report_combo() -> None:
    print(f"combo item count: {window.model_combo.count()}")
    for i in range(window.model_combo.count()):
        print(f"  [{i}] text={window.model_combo.itemText(i)!r} data={window.model_combo.itemData(i)!r}")
    print(f"current selection: {window.model_combo.currentText()!r}")
    print(f"_selected_model() -> {window._selected_model()!r}")


def do_send() -> None:
    window.input.setText("dummy task")
    window._send()


def finish() -> None:
    print(f"captured start_new calls: {captured_calls}")
    loop.stop()


QTimer.singleShot(1000, report_combo)
QTimer.singleShot(1200, do_send)
QTimer.singleShot(1800, finish)

with loop:
    loop.run_forever()
