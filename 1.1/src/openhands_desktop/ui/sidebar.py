"""Left sidebar: conversation history (from HistoryStore, grouped by day)
plus a search box and a "new conversation" button.

Clicking a past entry reattaches a live session to it (see
ConversationController.attach() / MainWindow._resume_conversation) -- it
re-syncs history via the same WS-first buffer-then-merge path a fresh
conversation uses, so the log fills back in and status polling picks up
wherever that conversation currently stands. Only one conversation is live
at a time (ConversationController is 1:1 with the window), so attaching to
one stops whatever was previously active.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.core.history_store import ConversationRecord
from openhands_desktop.ui.icons import icon
from openhands_desktop.ui.palette import COLOR_NEUTRAL, COLOR_PRIMARY
from openhands_desktop.ui.spacing import (
    CONVERSATION_ITEM_HEIGHT,
    SIDEBAR_MAX_WIDTH,
    SIDEBAR_MIN_WIDTH,
    SPACE_MD,
    SPACE_SM,
    SPACE_XS,
)

_ITEM_ID_ROLE = 1000


def _day_bucket(created_at_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return "Earlier"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta_days = (now.date() - dt.date()).days
    if delta_days == 0:
        return "Today"
    if delta_days == 1:
        return "Yesterday"
    return "Older"


class _DateHeaderWidget(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("ChipCaption")
        self.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, 2)


class ConversationItemWidget(QWidget):
    """One conversation row: title + status/model meta, with a selected
    state (blue border + tinted background + glow) toggled via a dynamic
    QSS property -- see #ConversationItem[selected="true"] in theme.py.

    This widget is an OUTER, unstyled wrapper around an INNER "card" widget
    (self._card, objectName ConversationItem) with a few pixels of margin
    between them. That margin exists specifically so a
    QGraphicsDropShadowEffect glow on the card has room to render into: an
    earlier attempt applied the effect directly to a widget that exactly
    filled its QListWidgetItem's bounds, and the list view's own clipping
    of that bounds cut the blurred shadow off partway, which visually read
    as the border being split into two separate boxes (a real, observed
    rendering bug, not a hypothetical one).
    """

    _GLOW_MARGIN = 6
    delete_requested = Signal(str)  # conversation_id

    def __init__(self, record: ConversationRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conversation_id = record.conversation_id
        self.setFixedHeight(CONVERSATION_ITEM_HEIGHT + self._GLOW_MARGIN * 2)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(
            self._GLOW_MARGIN, self._GLOW_MARGIN, self._GLOW_MARGIN, self._GLOW_MARGIN
        )

        self._card = QWidget()
        self._card.setObjectName("ConversationItem")
        self._card.setFixedHeight(CONVERSATION_ITEM_HEIGHT)
        outer_layout.addWidget(self._card)

        layout = QVBoxLayout(self._card)
        layout.setContentsMargins(SPACE_SM, SPACE_XS + 2, SPACE_SM, SPACE_XS + 2)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(SPACE_XS)
        bubble_icon = QLabel()
        bubble_icon.setPixmap(icon("conversations", 16, COLOR_NEUTRAL).pixmap(16, 16))
        title_row.addWidget(bubble_icon)

        title = QLabel(record.title or "(untitled)")
        title.setObjectName("ConversationItemTitle")
        title.setWordWrap(False)
        fm = title.fontMetrics()
        title.setText(fm.elidedText(record.title or "(untitled)", Qt.TextElideMode.ElideRight, 190))
        title_row.addWidget(title, 1)

        more_btn = QPushButton("⋯")  # midline horizontal ellipsis, not emoji
        more_btn.setObjectName("ConversationMoreButton")
        more_btn.setFixedSize(20, 20)
        more_btn.setToolTip("Conversation actions")
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.clicked.connect(lambda: self._show_actions_menu(more_btn))
        title_row.addWidget(more_btn)
        layout.addLayout(title_row)

        meta = QLabel(f"{record.last_status or 'unknown'} · {record.llm_model or 'unknown model'}")
        meta.setObjectName("ConversationItemMeta")
        meta.setContentsMargins(16 + SPACE_XS, 0, 0, 0)  # align under the title, past the bubble icon
        layout.addWidget(meta)
        layout.addStretch()

        self.setToolTip(
            f"id: {record.conversation_id}\ncreated: {record.created_at}\n"
            "Click to reattach a live session to this conversation."
        )

    def _show_actions_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.setObjectName("ConversationActionsMenu")
        delete_action = menu.addAction("Delete conversation")
        delete_action.triggered.connect(lambda: self.delete_requested.emit(self.conversation_id))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def set_selected(self, selected: bool) -> None:
        self._card.setProperty("selected", "true" if selected else "false")
        style = self._card.style()
        style.unpolish(self._card)
        style.polish(self._card)
        if selected:
            # A smaller, fully-opaque, zero-offset shadow reads as a even
            # halo hugging the border uniformly; a larger blur radius faded
            # unevenly (brighter near the top edge than the bottom), which
            # looked like the border itself was two different colors.
            glow = QGraphicsDropShadowEffect(self._card)
            glow_color = QColor(COLOR_PRIMARY)
            glow_color.setAlpha(200)
            glow.setColor(glow_color)
            glow.setBlurRadius(self._GLOW_MARGIN * 1.5)
            glow.setOffset(0, 0)
            self._card.setGraphicsEffect(glow)
        else:
            self._card.setGraphicsEffect(None)


class Sidebar(QWidget):
    new_conversation_requested = Signal()
    conversation_selected = Signal(str)  # conversation_id
    conversation_delete_requested = Signal(str)  # conversation_id
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        self.setMaximumWidth(max(SIDEBAR_MAX_WIDTH, 420))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        layout.setSpacing(SPACE_XS)

        # Brand icon + collapse control now live in IconRail, the narrow
        # column to the left of this sidebar -- not duplicated here anymore
        # (this used to render its own copy of both, with the collapse
        # button permanently disabled since nothing outside the sidebar
        # could bring it back once hidden).
        header_row = QHBoxLayout()
        header_label = QLabel("Conversations")
        self.new_btn = QPushButton()
        self.new_btn.setIcon(icon("add", 16))
        self.new_btn.setIconSize(QSize(16, 16))
        self.new_btn.setFixedWidth(32)
        self.new_btn.setToolTip("Start a new conversation")
        self.new_btn.clicked.connect(self.new_conversation_requested.emit)
        header_row.addWidget(header_label)
        header_row.addStretch()
        header_row.addWidget(self.new_btn)
        layout.addLayout(header_row)

        search_frame = QFrame()
        search_frame.setObjectName("SearchFrame")
        search_row = QHBoxLayout(search_frame)
        search_row.setContentsMargins(SPACE_XS + 2, 2, SPACE_XS, 2)
        search_row.setSpacing(SPACE_XS)
        search_icon_label = QLabel()
        search_icon_label.setPixmap(icon("search", 15).pixmap(15, 15))
        search_row.addWidget(search_icon_label)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchInput")
        self.search_box.setPlaceholderText("Search conversations…")
        self.search_box.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_box, 1)
        shortcut_label = QLabel("Ctrl+K")
        shortcut_label.setObjectName("ChipCaption")
        search_row.addWidget(shortcut_label)
        layout.addWidget(search_frame)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("ConversationList")
        self.list_widget.setSpacing(0)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

        layout.addSpacing(SPACE_XS)
        footer_row = QHBoxLayout()
        settings_btn = QPushButton("Settings")
        settings_btn.setIcon(icon("settings", 16))
        settings_btn.clicked.connect(self.settings_requested.emit)
        footer_row.addWidget(settings_btn)
        footer_row.addStretch()

        profile_chip = QFrame()
        profile_chip.setObjectName("ProfileChip")
        profile_row = QHBoxLayout(profile_chip)
        profile_row.setContentsMargins(6, 4, 8, 4)
        profile_row.setSpacing(SPACE_XS)
        avatar = QLabel("VP")
        avatar.setObjectName("ProfileAvatar")
        avatar.setFixedSize(24, 24)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        profile_row.addWidget(avatar)
        profile_name = QLabel("Local user")
        profile_row.addWidget(profile_name)
        profile_chip.setToolTip("Local single-user desktop client -- no login")
        footer_row.addWidget(profile_chip)

        layout.addLayout(footer_row)

        self._records: list[ConversationRecord] = []
        self._selected_id: str | None = None
        self._item_widgets: dict[str, ConversationItemWidget] = {}

    def set_records(self, records: list[ConversationRecord]) -> None:
        self._records = records
        self._rebuild(records)

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        if not text:
            self._rebuild(self._records)
            return
        filtered = [
            r for r in self._records
            if text in (r.title or "").lower() or text in (r.llm_model or "").lower()
        ]
        self._rebuild(filtered)

    def _rebuild(self, records: list[ConversationRecord]) -> None:
        self.list_widget.clear()
        self._item_widgets = {}
        current_bucket: str | None = None
        for record in records:
            bucket = _day_bucket(record.created_at)
            if bucket != current_bucket:
                current_bucket = bucket
                header_item = QListWidgetItem()
                header_item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(header_item)
                self.list_widget.setItemWidget(header_item, _DateHeaderWidget(bucket))

            item = QListWidgetItem()
            item.setData(_ITEM_ID_ROLE, record.conversation_id)
            item.setSizeHint(QSize(0, CONVERSATION_ITEM_HEIGHT + ConversationItemWidget._GLOW_MARGIN * 2))
            self.list_widget.addItem(item)
            item_widget = ConversationItemWidget(record)
            item_widget.set_selected(record.conversation_id == self._selected_id)
            item_widget.delete_requested.connect(self.conversation_delete_requested.emit)
            self.list_widget.setItemWidget(item, item_widget)
            self._item_widgets[record.conversation_id] = item_widget

        if not records:
            empty_item = QListWidgetItem()
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(empty_item)
            empty_label = QLabel("No conversations yet -- click + to start one.")
            empty_label.setObjectName("ConversationItemMeta")
            empty_label.setWordWrap(True)
            empty_label.setContentsMargins(SPACE_XS, SPACE_MD, SPACE_XS, SPACE_MD)
            self.list_widget.setItemWidget(empty_item, empty_label)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(_ITEM_ID_ROLE)
        if not conversation_id:
            return
        if self._selected_id and self._selected_id in self._item_widgets:
            self._item_widgets[self._selected_id].set_selected(False)
        self._selected_id = conversation_id
        self._item_widgets[conversation_id].set_selected(True)
        self.conversation_selected.emit(conversation_id)
