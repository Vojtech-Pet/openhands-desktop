from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import qasync
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.ui.main_window import MainWindow

# Any string is fine here -- it's just the name of the local (Unix domain)
# socket used as the single-instance lock, not a network port.
_SINGLE_INSTANCE_KEY = "openhands-desktop-singleton"


def _acquire_single_instance() -> QLocalServer | None:
    """None if another instance is already running (caller should exit);
    otherwise a QLocalServer the caller must keep alive for the app's
    lifetime (letting it get garbage-collected drops the lock).

    QLocalServer/QLocalSocket rather than a hand-rolled PID file: a PID
    file needs its own crash-recovery logic (is that PID still alive, or
    -- worse -- reused by an unrelated process since?). A Unix domain
    socket is released by the OS the moment its owning process dies, dead
    or alive, so there's no stale-lock case to handle by hand.
    """
    probe = QLocalSocket()
    probe.connectToServer(_SINGLE_INSTANCE_KEY)
    # 50ms, not 200ms: a Unix domain socket with no listener refuses the
    # connection almost instantly, so this is generous headroom, not a
    # tight race -- and it's the one synchronous wait that blocks the
    # window from appearing at all, on every single normal launch.
    if probe.waitForConnected(50):
        probe.disconnectFromServer()
        return None

    # No live instance answered: any socket file left on disk belongs to a
    # previous instance that crashed instead of shutting down cleanly.
    # Clearing it before listening is the standard idiom for this on Unix.
    QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)
    server = QLocalServer()
    server.listen(_SINGLE_INSTANCE_KEY)
    return server


def main() -> int:
    app = QApplication(sys.argv)
    app_icon = _app_icon()
    app.setWindowIcon(app_icon)

    singleton_server = _acquire_single_instance()
    if singleton_server is None:
        print("OpenHands Desktop is already running -- not starting a second copy.", file=sys.stderr)
        return 0

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # v1.1 (new Agent Server architecture, see MIGRATION_STATUS.md): one
    # shared server, not the old per-conversation sandbox model -- base
    # URL/key are env-driven for now while this is still an experiment,
    # not hardcoded like the old app-server's fixed port 3000 was.
    client = AppServerClient(
        base_url=os.environ.get("AGENT_SERVER_URL", "http://127.0.0.1:8010"),
        session_api_key=os.environ.get("AGENT_SERVER_SESSION_API_KEY"),
    )
    window = MainWindow(client)
    window.setWindowIcon(app_icon)
    window.show()

    # A second launch attempt connects to the socket above instead of
    # starting its own instance (see the waitForConnected branch) -- when
    # that happens, bring this, the real instance's window, to the front
    # rather than leaving the user wondering why nothing opened.
    def _on_second_launch_attempt() -> None:
        connection = singleton_server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        window.show()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    singleton_server.newConnection.connect(_on_second_launch_attempt)

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
