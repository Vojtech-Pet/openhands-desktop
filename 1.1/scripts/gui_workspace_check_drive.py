"""Drives the real MainWindow's workspace-preflight feature end to end,
bypassing only the native file-picker dialog (calling _run_workspace_check
directly with a real temp path instead) -- everything downstream (real
run_preflight, real QMessageBox, real log append) executes for real.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.core.workspace_preflight import SANDBOX_UID
from openhands_desktop.ui.main_window import MainWindow

app = QApplication(sys.argv)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)

client = AppServerClient()
window = MainWindow(client)
window.show()

tmp = tempfile.mkdtemp()
bad_dir = os.path.join(tmp, "no_acl")
os.makedirs(bad_dir, mode=0o755)


def trigger_check() -> None:
    window._run_workspace_check(bad_dir)


def close_modal_and_report() -> None:
    modal = QApplication.activeModalWidget()
    if modal is not None:
        print(f"modal dialog found: {modal.windowTitle()!r}, closing it")
        modal.close()
    print("---LOG CONTENTS---")
    print(window.log.toPlainText())
    loop.stop()


QTimer.singleShot(500, trigger_check)
QTimer.singleShot(1500, close_modal_and_report)

with loop:
    loop.run_forever()
