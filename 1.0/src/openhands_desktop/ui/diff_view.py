"""Repo-awareness + per-step diff review (borrowed from Cursor's Composer /
Windsurf's Cascade / Aider's repo map): a dialog listing every file the
agent has touched this session, with a real colored diff on selection --
not just a truncated text preview of the tool call. Backed by the real
GET .../git/changes + GET .../git/diff endpoints (confirmed live
2026-07-28); git/diff returns full {"original", "modified"} file contents,
not a unified diff, so the diff itself is computed client-side with
difflib.
"""

from __future__ import annotations

import difflib
import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.api.client import AppServerClient, DEFAULT_WORKSPACE_PATH
from openhands_desktop.ui.async_utils import run_async
from openhands_desktop.ui.palette import (
    BG_SURFACE_1,
    BG_SURFACE_2,
    BORDER,
    COLOR_DANGER,
    COLOR_SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from openhands_desktop.ui.spacing import RADIUS_MD, SPACE_SM, SPACE_XS

_STATUS_COLOR = {
    "ADDED": COLOR_SUCCESS,
    "MODIFIED": COLOR_SUCCESS,
    "UPDATED": COLOR_SUCCESS,
    "DELETED": COLOR_DANGER,
    "REMOVED": COLOR_DANGER,
}


def _diff_html(original: str, modified: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            lineterm="",
        )
    )
    if not diff_lines:
        return f'<span style="color:{TEXT_MUTED};">No textual changes.</span>'
    rows = []
    for line in diff_lines:
        text = html.escape(line.rstrip("\n")) or "&nbsp;"
        if line.startswith(("+++", "---")):
            color = TEXT_MUTED
        elif line.startswith("@@"):
            color = TEXT_SECONDARY
        elif line.startswith("+"):
            color = COLOR_SUCCESS
        elif line.startswith("-"):
            color = COLOR_DANGER
        else:
            color = TEXT_PRIMARY
        rows.append(f'<div style="color:{color};">{text}</div>')
    return "".join(rows)


class ChangesDialog(QDialog):
    """Master-detail: changed files on the left, a real diff on the right."""

    def __init__(
        self,
        parent: QWidget | None,
        client: AppServerClient,
        conversation_id: str,
        workspace_path: str = DEFAULT_WORKSPACE_PATH,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._conversation_id = conversation_id
        self._workspace_path = workspace_path

        self.setWindowTitle("Changes")
        self.resize(760, 480)
        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {BG_SURFACE_1}; }}
            QListWidget {{
                background-color: {BG_SURFACE_2};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_MD}px;
            }}
            QTextEdit {{
                background-color: {BG_SURFACE_2};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_MD}px;
                color: {TEXT_PRIMARY};
            }}
            """
        )

        root = QVBoxLayout(self)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel(f"Files touched in {workspace_path}"))
        header_row.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._reload)
        header_row.addWidget(refresh_btn)
        root.addLayout(header_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._list = QListWidget()
        self._list.setMaximumWidth(280)
        self._list.itemClicked.connect(self._on_item_clicked)
        splitter.addWidget(self._list)

        self._diff_view = QTextEdit()
        self._diff_view.setReadOnly(True)
        self._diff_view.setFontFamily("monospace")
        self._diff_view.setHtml(f'<span style="color:{TEXT_MUTED};">Select a file to view its diff.</span>')
        splitter.addWidget(self._diff_view)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        loading = QListWidgetItem("Loading…")
        loading.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(loading)
        run_async(
            self,
            self._client.get_git_changes(self._conversation_id, self._workspace_path),
            self._on_changes_loaded,
            error_title="Failed to load changes",
        )

    def _on_changes_loaded(self, changes: list[dict]) -> None:
        self._list.clear()
        if not changes:
            empty = QListWidgetItem("No changes yet.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty)
            return
        for change in changes:
            status = change.get("status", "?")
            path = change.get("path", "?")
            item = QListWidgetItem(f"{status[:1]}  {path}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            color = _STATUS_COLOR.get(status, TEXT_SECONDARY)
            item.setForeground(QColor(color))
            self._list.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        full_path = f"{self._workspace_path}/{path}"
        self._diff_view.setHtml(f'<span style="color:{TEXT_MUTED};">Loading diff…</span>')
        run_async(
            self,
            self._client.get_git_diff(self._conversation_id, full_path),
            self._on_diff_loaded,
            error_title="Failed to load diff",
        )

    def _on_diff_loaded(self, data: dict) -> None:
        original = data.get("original") or ""
        modified = data.get("modified") or ""
        self._diff_view.setHtml(_diff_html(original, modified))
