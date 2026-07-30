from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import qasync
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app_icon = _app_icon()
    app.setWindowIcon(app_icon)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    client = AppServerClient()
    window = MainWindow(client)
    window.setWindowIcon(app_icon)
    window.show()

    with loop:
        exit_code = loop.run_forever()

    return exit_code or 0


def _app_icon() -> QIcon:
    plan_icon = Path("/home/vojtech/.local/share/icons/hicolor/256x256/apps/openhands-plan.png")
    if plan_icon.exists():
        return QIcon(str(plan_icon))
    bundled_icon = Path(__file__).resolve().parent / "resources" / "icons" / "terminal.svg"
    return QIcon(str(bundled_icon))


if __name__ == "__main__":
    sys.exit(main())
