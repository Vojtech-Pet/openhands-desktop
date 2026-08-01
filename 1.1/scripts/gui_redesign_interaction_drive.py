"""Drives the redesigned MainWindow: click a real suggestion button (via
Qt's own click(), not synthetic text-setting) to confirm it fills the input,
then send it and confirm the view switches from the welcome screen to the
chat log, and that the sidebar's "+" resets back to welcome."""

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
    btn = window.welcome.findChildren(type(window.send_btn))
    # Find the actual suggestion buttons specifically (objectName SuggestionButton)
    suggestion_buttons = [b for b in btn if b.objectName() == "SuggestionButton"]
    print(f"found {len(suggestion_buttons)} suggestion buttons")
    assert suggestion_buttons, "no suggestion buttons found"
    suggestion_buttons[0].click()
    print(f"input after suggestion click: {window.input.toPlainText()!r}")
    assert window.input.toPlainText(), "clicking a suggestion did not populate the input"


def send_and_check() -> None:
    window._send()
    print(f"stack current widget is log: {window.stack.currentWidget() is window.log}")
    assert window.stack.currentWidget() is window.log, "view did not switch to chat log after send"
    print("PASS: suggestion -> input -> send -> view switches to chat log")


def reset_and_check() -> None:
    window.sidebar.new_conversation_requested.emit()
    print(f"after +: stack current widget is welcome: {window.stack.currentWidget() is window.welcome}")
    assert window.stack.currentWidget() is window.welcome, "+ did not reset back to welcome screen"
    assert window.agent_type_combo.isEnabled(), "+ did not re-enable the agent type combo"
    print("PASS: '+' resets back to the welcome screen for a fresh conversation")
    loop.stop()


QTimer.singleShot(800, click_suggestion)
QTimer.singleShot(1200, send_and_check)
QTimer.singleShot(4000, reset_and_check)

with loop:
    loop.run_forever()
