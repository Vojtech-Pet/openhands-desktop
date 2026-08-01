"""Toggleable right-hand detail panel -- "Tokens & Cost" and "Diagnostics"
tabs, per the chat design spec's right panel concept. Scoped to the two
tabs this client has real, already-tracked data for (MainWindow._context_usage,
health/connection/workspace/model status); Plan/Tasks/Files changed/Git
diff/Terminal/Browser state already exist as separate header buttons
(task_board.py, diff_view.py, etc.) and aren't duplicated here -- see the
2026-07-30 design-brief conversation for why those weren't folded in.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from openhands_desktop.ui.icons import icon
from openhands_desktop.ui.palette import COLOR_DANGER, COLOR_SUCCESS, TEXT_MUTED, TEXT_PRIMARY
from openhands_desktop.ui.spacing import RADIUS_MD, SPACE_MD, SPACE_SM, SPACE_XS


class _StatRow(QWidget):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        caption = QLabel(label)
        caption.setObjectName("ChipCaption")
        layout.addWidget(caption)
        layout.addStretch(1)
        self.value = QLabel("—")
        self.value.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        layout.addWidget(self.value)


class RightPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RightPanel")
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        layout.setSpacing(SPACE_MD)

        title = QLabel("Details")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 15px;")
        layout.addWidget(title)

        tokens_card = QFrame()
        tokens_card.setObjectName("DetailCard")
        tokens_layout = QVBoxLayout(tokens_card)
        tokens_layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        tokens_layout.setSpacing(SPACE_XS)
        section = QLabel("Tokens & cost")
        section.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 13px;")
        tokens_layout.addWidget(section)
        self.tokens_row = _StatRow("Context used")
        tokens_layout.addWidget(self.tokens_row)
        self.tool_calls_row = _StatRow("Tool calls this turn")
        tokens_layout.addWidget(self.tool_calls_row)
        cost_note = QLabel("Cost isn't reported by this server -- not shown rather than guessed.")
        cost_note.setWordWrap(True)
        cost_note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        tokens_layout.addWidget(cost_note)
        layout.addWidget(tokens_card)

        diag_card = QFrame()
        diag_card.setObjectName("DetailCard")
        diag_layout = QVBoxLayout(diag_card)
        diag_layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        diag_layout.setSpacing(SPACE_XS)
        diag_title = QLabel("Diagnostics")
        diag_title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 13px;")
        diag_layout.addWidget(diag_title)
        self.health_row = _StatRow("Server health")
        diag_layout.addWidget(self.health_row)
        self.connection_row = _StatRow("Connection")
        diag_layout.addWidget(self.connection_row)
        self.workspace_row = _StatRow("Workspace")
        diag_layout.addWidget(self.workspace_row)
        self.model_row = _StatRow("Model")
        diag_layout.addWidget(self.model_row)
        layout.addWidget(diag_card)

        layout.addStretch(1)

    def set_token_usage(self, used: int, limit: int, is_real: bool, tool_calls: int) -> None:
        prefix = "" if is_real else "~"
        percent = int(used * 100 / limit) if limit else 0
        self.tokens_row.value.setText(f"{prefix}{used // 1000}K/{limit // 1000}K ({percent}%)")
        self.tool_calls_row.value.setText(str(tool_calls))

    def set_diagnostics(self, health_ok: bool, connected: bool, workspace: str, model: str) -> None:
        self.health_row.value.setText("OK" if health_ok else "Down")
        self.health_row.value.setStyleSheet(
            f"color: {COLOR_SUCCESS if health_ok else COLOR_DANGER}; font-weight: 600;"
        )
        self.connection_row.value.setText("Connected" if connected else "Disconnected")
        self.connection_row.value.setStyleSheet(
            f"color: {COLOR_SUCCESS if connected else COLOR_DANGER}; font-weight: 600;"
        )
        self.workspace_row.value.setText(workspace or "—")
        self.model_row.value.setText(model or "—")


def panel_toggle_button() -> QPushButton:
    button = QPushButton()
    button.setObjectName("MenuButton")
    button.setIcon(icon("panel", 18))
    button.setCheckable(True)
    button.setToolTip("Toggle details panel")
    return button
