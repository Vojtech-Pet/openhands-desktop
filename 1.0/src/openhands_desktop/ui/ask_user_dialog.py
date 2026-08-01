"""Modal shown when the agent calls the ask_user_question MCP tool.

Deliberately answer-or-dismiss: the agent is blocked waiting on this, so
every exit path must resolve the pending question -- closing the window
counts as "dismissed" rather than leaving the run hung.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.ui.palette import (
    BG_SURFACE_1,
    BG_SURFACE_2,
    BORDER,
    BORDER_HOVER,
    COLOR_PRIMARY,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from openhands_desktop.ui.spacing import RADIUS_MD, SPACE_MD, SPACE_SM, SPACE_XS


class AskUserDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        question: str,
        options: list[str],
        multi_select: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("The agent has a question")
        self.setMinimumWidth(460)
        self._answer: str | None = None
        self._multi_select = multi_select
        self._checkboxes: list[QCheckBox] = []

        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {BG_SURFACE_1}; }}
            QLabel {{ color: {TEXT_PRIMARY}; }}
            #AskOption {{
                background-color: {BG_SURFACE_2};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_MD}px;
                padding: 10px 14px;
                text-align: left;
                color: {TEXT_PRIMARY};
            }}
            #AskOption:hover {{
                border-color: {COLOR_PRIMARY};
                background-color: {BORDER};
            }}
            QLineEdit {{
                background-color: {BG_SURFACE_2};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_MD}px;
                padding: 8px 10px;
                color: {TEXT_PRIMARY};
            }}
            QLineEdit:focus {{ border-color: {COLOR_PRIMARY}; }}
            QCheckBox {{
                background-color: {BG_SURFACE_2};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_MD}px;
                padding: 10px 14px;
                color: {TEXT_PRIMARY};
            }}
            QCheckBox:hover {{ border-color: {COLOR_PRIMARY}; }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        layout.setSpacing(SPACE_SM)

        prompt = QLabel(question)
        prompt.setWordWrap(True)
        prompt.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {TEXT_PRIMARY};")
        layout.addWidget(prompt)

        if multi_select:
            for option in options:
                checkbox = QCheckBox(option)
                checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
                self._checkboxes.append(checkbox)
                layout.addWidget(checkbox)
            confirm = QPushButton("Confirm selection")
            confirm.setObjectName("AskOption")
            confirm.setCursor(Qt.CursorShape.PointingHandCursor)
            confirm.clicked.connect(self._choose_checked)
            layout.addWidget(confirm)
        else:
            for option in options:
                button = QPushButton(option)
                button.setObjectName("AskOption")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, value=option: self._choose(value))
                layout.addWidget(button)

        hint = QLabel("or type your own answer:")
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(SPACE_XS)
        self._free_text = QLineEdit()
        self._free_text.setPlaceholderText("Something else…")
        self._free_text.returnPressed.connect(self._choose_free_text)
        row.addWidget(self._free_text, 1)
        send = QPushButton("Send")
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.clicked.connect(self._choose_free_text)
        row.addWidget(send)
        layout.addLayout(row)

    def _choose_checked(self) -> None:
        selected = [c.text() for c in self._checkboxes if c.isChecked()]
        if selected:
            self._choose(", ".join(selected))

    def _choose(self, value: str) -> None:
        self._answer = value
        self.accept()

    def _choose_free_text(self) -> None:
        text = self._free_text.text().strip()
        if text:
            self._choose(text)

    @property
    def answer(self) -> str | None:
        return self._answer
