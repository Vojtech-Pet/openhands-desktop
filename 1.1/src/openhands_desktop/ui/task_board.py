"""Mission Control (borrowed from GitHub Copilot's task dashboard, Devin's
parallel-session view): every conversation on the server with its live
execution_status in one list, not just the sidebar's local HistoryStore-
backed chronological view -- so several running tasks read at a glance,
and one that isn't in local history (e.g. started from another client) is
still reachable. Backed by the real GET /api/v1/app-conversations/search
endpoint on the current OpenHands app-server.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.models import AppConversation, ExecutionStatus, resolve_conversation_family
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


def _status_row(conversation: AppConversation, on_delete) -> QWidget:
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
    status_label = QLabel(status_text)
    status_label.setStyleSheet(f"color: {color}; font-size: 12.5px; font-weight: 600;")
    layout.addWidget(status_label)

    more_btn = QPushButton("⋯")
    more_btn.setFixedSize(20, 20)
    more_btn.setToolTip("Task actions")
    more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    more_btn.clicked.connect(lambda: _show_task_menu(more_btn, conversation.id, on_delete))
    layout.addWidget(more_btn)

    return row


def _show_task_menu(anchor: QWidget, conversation_id: str, on_delete) -> None:
    menu = QMenu(anchor)
    delete_action = menu.addAction("Delete conversation")
    delete_action.triggered.connect(lambda: on_delete(conversation_id))
    menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class TaskBoardDialog(QDialog):
    open_requested = Signal(str)  # conversation_id
    conversations_deleted = Signal(list)  # list[str] of conversation_id -- lets
    # MainWindow notice and reset itself if the conversation it's currently
    # showing (log, Errors count, status pill) was deleted from here instead
    # of from its own sidebar -- this dialog has no other way to tell it.

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

        self._conversations: list[AppConversation] = []
        self._deleting_all_ids: list[str] = []

        layout = QVBoxLayout(self)
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("All conversations on this server, live"))
        header_row.addStretch()
        self._delete_all_btn = QPushButton("Delete all")
        self._delete_all_btn.setStyleSheet(f"QPushButton {{ color: {COLOR_DANGER}; }}")
        self._delete_all_btn.setEnabled(False)
        self._delete_all_btn.clicked.connect(self._confirm_delete_all)
        header_row.addWidget(self._delete_all_btn)
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
        self._conversations = conversations
        self._delete_all_btn.setEnabled(bool(conversations))
        if not conversations:
            empty = QListWidgetItem("No conversations on this server yet.")
            self._list.addItem(empty)
            return
        for conversation in conversations:
            item = QListWidgetItem()
            item.setData(1000, conversation.id)
            row = _status_row(conversation, self._confirm_delete_task)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(1000)
        if conversation_id:
            self.open_requested.emit(conversation_id)
            self.accept()

    def _confirm_delete_task(self, conversation_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete conversation",
            "Delete this conversation? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            run_async(
                self,
                self._delete_family(conversation_id),
                self._on_delete_family_done,
                error_title="Failed to delete conversation",
            )

    async def _delete_family(self, conversation_id: str) -> set[str]:
        family_ids = resolve_conversation_family(conversation_id, self._conversations)
        for family_id in family_ids:
            try:
                await self._client.delete_conversation(family_id)
            except Exception:  # noqa: BLE001 -- keep deleting the rest of the family
                pass
        return family_ids

    def _on_delete_family_done(self, family_ids: set[str]) -> None:
        self._reload()
        self.conversations_deleted.emit(list(family_ids))

    def _confirm_delete_all(self) -> None:
        count = len(self._conversations)
        if count == 0:
            return
        reply = QMessageBox.question(
            self,
            "Delete all conversations",
            f"Delete all {count} conversation(s) on this server? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._delete_all_btn.setEnabled(False)
            self._list.clear()
            self._list.addItem(QListWidgetItem(f"Deleting {count} conversation(s)… waiting"))
            self._deleting_all_ids = [c.id for c in self._conversations]
            run_async(
                self,
                self._delete_all(),
                self._on_delete_all_done,
                error_title="Failed to delete all conversations",
            )

    async def _delete_all(self) -> tuple[int, int]:
        deleted = 0
        failed = 0
        for conversation in self._conversations:
            try:
                await self._client.delete_conversation(conversation.id)
                deleted += 1
            except Exception:  # noqa: BLE001 -- keep deleting the rest, report the tally after
                failed += 1
        return deleted, failed

    def _on_delete_all_done(self, result: tuple[int, int]) -> None:
        deleted, failed = result
        self._reload()
        self.conversations_deleted.emit(self._deleting_all_ids)
        if failed:
            QMessageBox.warning(
                self,
                "Some deletions failed",
                f"Deleted {deleted} conversation(s), but {failed} failed -- "
                "they're still in the list below. Try again, or check the app log for why.",
            )
