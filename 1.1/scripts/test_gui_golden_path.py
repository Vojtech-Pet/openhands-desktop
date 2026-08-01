"""Real GUI click-through test: instantiates the actual MainWindow, types a
message with QTest, clicks Send, and waits for a real reply to land in the
log view -- against the real dev agent-server + real LM Studio, not mocked.
Runs offscreen (QT_QPA_PLATFORM=offscreen), not part of the shipped app.

Uses an isolated history DB (not the real one) to avoid touching v1.0's
data.
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AGENT_SERVER_URL"] = "http://127.0.0.1:8010"
os.environ["AGENT_SERVER_SESSION_API_KEY"] = "dev-key-123"

import qasync  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from openhands_desktop.api.client import AppServerClient  # noqa: E402
from openhands_desktop.core.completion import RunState  # noqa: E402
from openhands_desktop.core.history_store import HistoryStore  # noqa: E402
from openhands_desktop.ui.main_window import MainWindow  # noqa: E402


async def main() -> int:
    tmp_db = tempfile.mktemp(suffix=".db")
    client = AppServerClient(base_url="http://127.0.0.1:8010", session_api_key="dev-key-123")
    history = HistoryStore(db_path=tmp_db)
    window = MainWindow(client, history_store=history)
    window.show()

    # Model list comes from LM Studio detection on a timer -- give it a
    # moment, then select the loaded model directly rather than waiting on
    # the full polling cycle.
    await asyncio.sleep(3)
    print(f"model_combo has {window.model_combo.count()} entries")
    for i in range(window.model_combo.count()):
        print(f"  [{i}] {window.model_combo.itemText(i)} -> {window.model_combo.itemData(i)}")
    if window.model_combo.count() > 0:
        window.model_combo.setCurrentIndex(0)

    QTest.keyClicks(window.input, "Say hello in exactly one short sentence, then call finish.")
    from PySide6.QtCore import Qt

    QTest.mouseClick(window.send_btn, Qt.MouseButton.LeftButton)

    print("clicked Send -- waiting for a real reply...")
    log_widget = window.log.widget()
    for i in range(90):
        await asyncio.sleep(2)
        row_count = log_widget.layout().count() if log_widget and log_widget.layout() else 0
        state = window._last_run_state
        print(f"  [{i * 2}s] state={state}, log rows={row_count}")
        if state in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED, RunState.ERROR):
            break

    print(f"\nfinal state: {window._last_run_state}, log rows: {row_count}")

    passed = window._last_run_state in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED)
    if window._controller and window._controller.conversation_id:
        try:
            await client.delete_conversation(window._controller.conversation_id)
        except Exception:  # noqa: BLE001
            pass
    await client.aclose()
    window.close()
    return 0 if passed else 1


if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        code = loop.run_until_complete(main())
    print("GUI TEST PASSED" if code == 0 else "GUI TEST FAILED")
    sys.exit(code)
