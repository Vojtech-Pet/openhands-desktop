"""Grabs a real screenshot of the running MainWindow (offscreen render) for
docs/screenshot.png. Not part of the shipped app.
"""

import asyncio
import os
import sys

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import qasync  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from openhands_desktop.api.client import AppServerClient  # noqa: E402
from openhands_desktop.core.history_store import HistoryStore  # noqa: E402
from openhands_desktop.ui.main_window import MainWindow  # noqa: E402


async def main() -> None:
    client = AppServerClient(base_url="http://127.0.0.1:3000")
    history = HistoryStore(db_path="/tmp/screenshot-history.db")
    window = MainWindow(client, history_store=history)
    window.resize(1930, 965)
    window.show()
    await asyncio.sleep(4)  # let model list / async setup settle
    if window.model_combo.count() > 1:
        window.model_combo.setCurrentIndex(1)
    await asyncio.sleep(1)
    pixmap = window.grab()
    pixmap.save("docs/screenshot.png", "PNG")
    print(f"saved docs/screenshot.png ({pixmap.width()}x{pixmap.height()})")
    await client.aclose()
    window.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(main())
