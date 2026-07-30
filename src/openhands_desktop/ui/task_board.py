"""Mission Control (borrowed from GitHub Copilot's task dashboard, Devin's
parallel-session view): every conversation on the server with its live
execution_status/sandbox_status in one list, not just the sidebar's local
HistoryStore-backed chronological view -- so several running tasks read at
a glance, and one that isn't in local history (e.g. started from another
client) is still reachable. Backed by the real
GET /api/v1/app-conversations/search endpoint.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.models import AppConversation, ExecutionStatus
from openhands_desktop.ui.async_utils import run_async
from openhands_desktop.ui.palette import (
    BG_SURFACE_1,
    BG_SURFACE_2,
    BORDER,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from openhands_desktop.ui.spacing import RADIUS_MD, SPACE_SM, SPACE_XS

_STATUS_COLOR = {
    ExecutionStatus.RUNNING: COLOR_PRIMARY,
    ExecutionStatus.WAITING_FOR_CONFIRMATION: COLOR_WARNING,
    ExecutionStatus.FINISHED: COLOR_SUCCESS,
    ExecutionStatus.ERROR: COLOR_DANGER,
    ExecutionStatus.STUCK: COLOR_DANGER,
    ExecutionStatus.PAUSED: TEXT_MUTED,
    ExecutionStatus.IDLE: TEXT_MUTED,
    ExecutionStatus.DELETING: TEXT_MUTED,
}


def _status_row(conversation: AppConversation) -> QWidget:
    row = QWidget()
    row.setObjectName("TaskRow")
    color = _STATUS_COLOR.get(conversation.execution_status, TEXT_MUTED)
    row.setStyleSheet(
        f"#TaskRow {{ background-color: {BG_SURFACE_2}; border: 1px solid {BORDER}; "
        f"border-left: 3px solid {color}; border-radius: {RADIUS_MD}px; }}"
    )
    layout = QHBoxLayout(row)
    layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)

    text_col = QVBoxLayout()
    text_col.setSpacing(0)
    title_label = QLabel(conversation.title or conversation.id[:8])
    title_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
    text_col.addWidget(title_label)
    model = conversation.llm_model or "unknown model"
    updated = conversation.raw.get("updated_at", "")
    detail_label = QLabel(f"{model} · updated {updated}")
    detail_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
    text_col.addWidget(detail_label)
    layout.addLayout(text_col, 1)

    status_text = conversation.execution_status.value if conversation.execution_status else "unknown"
    status_label = QLabel(f"{status_text}  ·  {conversation.sandbox_status}")
    status_label.setStyleSheet(f"color: {color}; font-size: 12.5px; font-weight: 600;")
    layout.addWidget(status_label)

    return row


class TaskBoardDialog(QDialog):
    open_requested = Signal(str)  # conversation_id

    def __init__(self, parent: QWidget | None, client: AppServerClient) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("Mission Control")
        self.resize(620, 480)
        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {BG_SURFACE_1}; }}
            QListWidget {{
                background-color: transparent;
                border: none;
            }}
            QListWidget::item {{ margin: 3px 0px; }}
            """
        )

        layout = QVBoxLayout(self)
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("All conversations on this server, live"))
        header_row.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._reload)
        header_row.addWidget(refresh_btn)
        layout.addLayout(header_row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list)

        hint = QLabel("Double-click a task to attach a live session to it.")
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(hint)

        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        loading = QListWidgetItem("Loading…")
        self._list.addItem(loading)
        run_async(self, self._client.search_conversations(), self._on_loaded, error_title="Failed to load tasks")

    def _on_loaded(self, conversations: list[AppConversation]) -> None:
        self._list.clear()
        conversations = sorted(conversations, key=lambda c: c.raw.get("updated_at", ""), reverse=True)
        if not conversations:
            empty = QListWidgetItem("No conversations on this server yet.")
            self._list.addItem(empty)
            return
        for conversation in conversations:
            item = QListWidgetItem()
            item.setData(1000, conversation.id)
            item.setSizeHint(_status_row(conversation).sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, _status_row(conversation))

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(1000)
        if conversation_id:
            self.open_requested.emit(conversation_id)
            self.accept()
