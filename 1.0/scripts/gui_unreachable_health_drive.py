"""Points the client at an unreachable port to verify the Health chip really
does turn red with a different icon when the server can't be reached --
without touching the real running server."""

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

# Deliberately unreachable port (nothing listens here) -- not the real
# server (3000). A high, unprivileged, unused port refuses the connection
# immediately instead of the OS-dependent hang a privileged port can cause.
client = AppServerClient(base_url="http://127.0.0.1:59999")
window = MainWindow(client)
window.show()


def report() -> None:
    print(f"health_label text: {window.health_label.text()!r}")
    print(f"health_label objectName: {window.health_label.objectName()!r}")
    print(f"connection_status_label text: {window.connection_status_label.text()!r}")


QTimer.singleShot(2000, report)

with loop:
    loop.run_forever()
