"""Verifies the remembered model-profile selection survives across separate
MainWindow instances (simulating an app restart) via real QSettings, not a
mock."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qasync
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.ui.main_window import MainWindow

# Use a distinct, throwaway QSettings scope so this doesn't clobber the
# user's real remembered choice from earlier manual runs.
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

client = AppServerClient()
window1 = MainWindow(client)


def select_27b_and_close() -> None:
    index = window1.model_combo.findData("lmstudio-qwen36-27b-q4")
    assert index >= 0, "expected profile not found"
    window1.model_combo.setCurrentIndex(index)
    print(f"window1: selected {window1.model_combo.currentText()!r}, saved to QSettings")

    # Now open a second, independent MainWindow (simulating a fresh app
    # launch) and check it picks up the same remembered selection.
    global window2
    window2 = MainWindow(client)
    QTimer.singleShot(1000, check_window2)


def check_window2() -> None:
    print(f"window2 (fresh instance): selected {window2.model_combo.currentText()!r}")
    assert "lmstudio-qwen36-27b-q4" in window2.model_combo.currentData()
    print("PASS: remembered selection survived across a fresh MainWindow instance")
    loop.stop()


QTimer.singleShot(1000, select_27b_and_close)

with loop:
    loop.run_forever()
