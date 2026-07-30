"""Regression check after the full visual redesign: suggestion click fills
the (now multi-line) composer input, send switches from the hero panel to
the chat log, '+' resets back -- same checks as before the redesign, to
confirm the functional wiring survived the layout rewrite."""

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


def click_suggestion() -> None:
    assert window.stack.currentWidget() is window.welcome, "should start on welcome screen"
    buttons = window.welcome.findChildren(type(window.send_btn))
    suggestion_buttons = [b for b in buttons if b.objectName() == "SuggestionButton"]
    print(f"found {len(suggestion_buttons)} suggestion buttons (expect 6)")
    assert len(suggestion_buttons) == 6
    suggestion_buttons[1].click()  # "Plan a change"
    print(f"input after suggestion click: {window.input.toPlainText()!r}")
    assert window.input.toPlainText() == "Plan a change"


def send_and_check() -> None:
    window._send()
    print(f"stack switched to log: {window.stack.currentWidget() is window.log}")
    assert window.stack.currentWidget() is window.log


def reset_and_check() -> None:
    window.sidebar.new_conversation_requested.emit()
    print(f"'+' reset to welcome: {window.stack.currentWidget() is window.welcome}")
    assert window.stack.currentWidget() is window.welcome
    assert window.agent_type_combo.isEnabled()

    # Health chip + workspace preflight still wired
    window._run_workspace_check("/tmp")
    print(f"workspace_value_label after check: {window.workspace_value_label.text()!r}")
    assert window.workspace_value_label.text()

    print("PASS: all functional wiring survived the redesign")
    loop.stop()


QTimer.singleShot(800, click_suggestion)
QTimer.singleShot(1200, send_and_check)
QTimer.singleShot(4000, reset_and_check)

with loop:
    loop.run_forever()
