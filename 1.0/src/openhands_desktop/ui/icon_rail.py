"""Narrow icon rail, left of the conversations sidebar -- per the chat
design reference's leftmost column. A new, separate widget rather than a
change inside sidebar.py: that file's conversation list carries carefully
tuned, previously-debugged behavior (selection glow, filtering, day
grouping -- see its own module docstring), so this stays purely additive
instead of touching it.

Only icons wired to something this app actually does are included (worth
noting explicitly: the reference mockup also shows a plain "grid" icon with
no obvious counterpart here, and it's deliberately left out rather than
added as a click-does-nothing decoration).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from openhands_desktop.ui.icons import tile_icon
from openhands_desktop.ui.spacing import SPACE_SM, SPACE_XS

RAIL_WIDTH = 64


class IconRail(QWidget):
    workspace_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IconRail")
        self.setFixedWidth(RAIL_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XS, SPACE_SM, SPACE_XS, SPACE_SM)
        layout.setSpacing(SPACE_SM)

        brand = QPushButton()
        brand.setObjectName("RailBrand")
        brand.setIcon(tile_icon("terminal", 30))
        brand.setIconSize(QSize(30, 30))
        brand.setFixedSize(44, 44)
        brand.setEnabled(False)
        brand.setToolTip("OpenHands Desktop")
        layout.addWidget(brand)
        layout.addSpacing(SPACE_SM)

        workspace_btn = self._rail_button("workspace", "Workspace preflight")
        workspace_btn.clicked.connect(self.workspace_requested.emit)
        layout.addWidget(workspace_btn)

        layout.addStretch(1)

        settings_btn = self._rail_button("settings", "Settings")
        settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(settings_btn)

    def _rail_button(self, icon_name: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("RailButton")
        button.setIcon(tile_icon(icon_name, 26))
        button.setIconSize(QSize(26, 26))
        button.setFixedSize(44, 44)
        button.setToolTip(tooltip)
        return button
