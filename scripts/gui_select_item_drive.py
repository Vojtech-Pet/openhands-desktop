"""Programmatically selects the first sidebar conversation item and takes a
screenshot-ready state, so the selected-background color change can be
verified without needing real mouse clicks."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.core.history_store import ConversationRecord
from openhands_desktop.ui.main_window import MainWindow

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

client = AppServerClient()
window = MainWindow(client)
window.show()


def select_first() -> None:
    window.sidebar.set_records([
        ConversationRecord("conv-1", "openai/lmstudio-qwen36-27b-q4", "Plan a change", "2026-07-27T20:00:00", "RUNNING"),
        ConversationRecord("conv-2", "openai/lmstudio-qwen36-27b-q4", "Debug API issue", "2026-07-26T20:00:00", "COMPLETED"),
    ])
    item = window.sidebar.list_widget.item(1)  # first real conversation row (0 is the "Today" header)
    window.sidebar._on_item_clicked(item)
    print("selected conv-1, now stays visible for screenshot")


QTimer.singleShot(600, select_first)

with loop:
    loop.run_forever()
