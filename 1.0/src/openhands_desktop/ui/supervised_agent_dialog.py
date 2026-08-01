"""Dialog for launching a task on a REAL OpenHands conversation, supervised
by control.py's hash-based repeat detection / progress scoring /
1-nudge-then-stop policy (see openhands_supervisor.py) -- OpenHands keeps its
full toolset (browser, VSCode, terminal, real sandbox); this only watches its
event stream and steps in when it loops instead of trusting it unattended.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.supervised_agent.openhands_supervisor import supervise_openhands_task


class SupervisedAgentDialog(QDialog):
    # supervise_openhands_task's on_step callback fires from the same
    # asyncio/qasync loop this dialog runs on, so a direct call would
    # normally be fine -- the signal is kept anyway for a single consistent
    # thread-safety story with the rest of this dialog's callbacks.
    _step_event = Signal(dict)
    _finished_event = Signal(object, object)  # (result_dict, error_or_None)

    def __init__(self, client: AppServerClient, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("Supervised Agent")
        self.setMinimumSize(700, 500)
        self._running = False
        self._step_event.connect(self._on_step_event)
        self._finished_event.connect(self._on_finished_event)

        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Starts a real OpenHands conversation (its own sandbox, own "
                "toolset) and watches it -- interrupts + redirects once on a "
                "repeated tool call, stops for good if it repeats again."
            )
        )

        layout.addWidget(QLabel("Task:"))
        self.task_field = QPlainTextEdit()
        self.task_field.setPlaceholderText("Describe what the agent should do…")
        self.task_field.setMaximumHeight(100)
        layout.addWidget(self.task_field)

        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._on_run_clicked)
        layout.addWidget(self.run_btn)

        layout.addWidget(QLabel("Log:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 12px;")
        layout.addWidget(self.log_view, 1)

    def _on_run_clicked(self) -> None:
        if self._running:
            return
        task = self.task_field.toPlainText().strip()
        if not task:
            self.log_view.append("ERROR: task is required.")
            return
        self._running = True
        self.run_btn.setEnabled(False)
        self.log_view.clear()
        asyncio.ensure_future(self._run_async(task))

    async def _run_async(self, task: str) -> None:
        def on_step(event: dict) -> None:
            self._step_event.emit(event)

        try:
            result = await supervise_openhands_task(task, self._client, on_step=on_step)
        except Exception as exc:  # noqa: BLE001
            self._finished_event.emit(None, exc)
            return
        self._finished_event.emit(result, None)

    def _on_step_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "action":
            self.log_view.append(
                f"action: {event['tool']}({event['arguments']}) "
                f"[seen {event['repeat_count']}x]"
            )
        elif kind == "observation":
            dup = " [DUPLICATE RESULT]" if event.get("duplicate_result") else ""
            self.log_view.append(f"  -> {event.get('tool')}: {event.get('preview', '')[:200]}{dup}")
        elif kind == "nudge":
            self.log_view.append(f"NUDGE: repeated call to {event['tool']} -- interrupting and redirecting")
        elif kind == "stuck":
            self.log_view.append(f"STUCK: {event['reason']}")
        elif kind == "state":
            self.log_view.append(f"[state: {event['state']}]")
        elif kind == "error":
            self.log_view.append(f"ERROR: {event['message']}")

    def _on_finished_event(self, result: dict | None, error: Exception | None) -> None:
        if error is not None:
            self.log_view.append(f"ERROR: {error}")
        elif result is not None:
            self.log_view.append("\n=== RESULT ===")
            self.log_view.append(str(result))
        self._running = False
        self.run_btn.setEnabled(True)
