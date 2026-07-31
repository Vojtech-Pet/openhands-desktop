"""Conversation log rendered as a vertical timeline: each turn gets a
time+dot rail on the left and a full-width card on the right, per the
2026-07-30 design reference (openhands_neon_svg_kit22). Each event is its
own card -- unlike the previous bubble-log, tool_call and tool_result are
no longer merged into one ever-growing "agent turn" bubble; instead a
tool_call opens a card with a Running status pill, and the matching
tool_result/error flips that same card's pill to Success/Error and fills
in its body. This mirrors what the client actually knows (a tool call is
outstanding until its observation arrives) instead of just grouping by
consecutive kind.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.ui.icons import thinking_icon, tile_icon, tool_accent_color, tool_call_icon
from openhands_desktop.ui.palette import (
    BORDER,
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_THINKING_ACCENT,
    COLOR_THINKING_BG,
    COLOR_THINKING_BORDER,
    COLOR_THINKING_TEXT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from openhands_desktop.ui.spacing import RADIUS_MD, SPACE_MD, SPACE_SM, SPACE_XS, SPACE_XXS

_RAIL_WIDTH = 64
_LONG_RUN_WRAP = 80
_PLAIN_ROW_COLLAPSE_THRESHOLD = 400


def _with_soft_wrap_points(text: str) -> str:
    """Let Qt wrap long command-like text that has no natural spaces."""
    pieces: list[str] = []
    run: list[str] = []
    for ch in text:
        if ch.isspace():
            if run:
                pieces.append(_break_run("".join(run)))
                run.clear()
            pieces.append(ch)
        else:
            run.append(ch)
    if run:
        pieces.append(_break_run("".join(run)))
    return "".join(pieces)


def _break_run(text: str) -> str:
    if len(text) <= _LONG_RUN_WRAP:
        return text
    return "​".join(text[i : i + _LONG_RUN_WRAP] for i in range(0, len(text), _LONG_RUN_WRAP))


def _dot_pixmap(color: str, size: int = 8) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


def _check_circle_pixmap(size: int = 16, color: str = "#23C995") -> QPixmap:
    """Outlined check-in-circle beside the Command line, matching
    tool-call-card.svg's reference icon exactly (not a filled dot)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = painter.pen()
    pen.setColor(QColor(color))
    pen.setWidthF(size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    margin = size * 0.12
    painter.drawEllipse(pixmap.rect().adjusted(int(margin), int(margin), -int(margin), -int(margin)))
    painter.drawPolyline([
        QPointF(size * 0.28, size * 0.52),
        QPointF(size * 0.44, size * 0.68),
        QPointF(size * 0.74, size * 0.34),
    ])
    painter.end()
    return pixmap


# kind -> (card border color, header label, header text color)
_CARD_STYLE = {
    "user": (BORDER, "You", TEXT_PRIMARY),
    # Purple ("AI model/agentic" in the palette), not green -- green reads
    # as "success/completed" elsewhere in this app, and now that thinking
    # text also renders in an "agent"-styled row, that association would be
    # actively misleading for a mid-task reasoning burst that isn't done.
    "agent": (COLOR_ACCENT, "Agent", TEXT_PRIMARY),
    "error": (COLOR_DANGER, "Error", COLOR_DANGER),
    "system": (BORDER, None, TEXT_MUTED),
}

_STATUS_COLORS = {
    "running": COLOR_PRIMARY,
    "success": COLOR_SUCCESS,
    "error": COLOR_DANGER,
    "cancelled": TEXT_MUTED,
}
_STATUS_LABELS = {
    "running": "Running",
    "success": "Success",
    "error": "Error",
    "cancelled": "Cancelled",
}


class _StatusPill(QWidget):
    """Small dot+text badge -- status is a badge on the card, never the
    card's own fill color (a whole-card green wash would drown out the
    tool-type color the icon/border already carries)."""

    def __init__(self, status: str = "running") -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._text = QLabel()
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        color = _STATUS_COLORS.get(status, TEXT_MUTED)
        self._dot.setPixmap(_dot_pixmap(color, 8))
        self._text.setText(_STATUS_LABELS.get(status, status.title()))
        self._text.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;"
        )


class LogView(QScrollArea):
    # Emitted when the user clicks Retry on an error card -- the view has
    # no controller reference itself, so the owner (MainWindow) is the one
    # that actually resends the last message.
    retry_requested = Signal()

    _AT_BOTTOM_TOLERANCE_PX = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        self._layout.setSpacing(SPACE_MD)
        self._layout.addStretch(1)
        self.setWidget(container)

        # Floating "jump to bottom" button -- shown only when scrolled up
        # away from the live edge (so it doesn't just sit there uselessly
        # while already following), a child of the QScrollArea itself
        # (viewport-relative position, not part of the scrolled content) so
        # it stays anchored to the bottom-right corner as the log scrolls.
        self._scroll_to_bottom_btn = QPushButton("↓", self)
        self._scroll_to_bottom_btn.setFixedSize(36, 36)
        self._scroll_to_bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scroll_to_bottom_btn.setToolTip("Scroll to the latest message")
        self._scroll_to_bottom_btn.setStyleSheet(
            "QPushButton { background-color: rgba(10, 14, 22, 220); color: #FFFFFF; "
            "border: 1px solid rgba(255, 255, 255, 40); border-radius: 18px; font-size: 15px; }"
            "QPushButton:hover { background-color: rgba(30, 36, 48, 235); }"
        )
        self._scroll_to_bottom_btn.clicked.connect(self._scroll_to_bottom_now)
        self._scroll_to_bottom_btn.hide()
        self.verticalScrollBar().valueChanged.connect(self._update_scroll_to_bottom_btn)
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self._update_scroll_to_bottom_btn())

        self._entries: list[str] = []  # plain-text mirror, for tests/back-compat

        # The most recently opened tool_call card, waiting for its matching
        # tool_result/error observation -- None once resolved. A single
        # in-flight slot is correct for this client: OpenHands actions are
        # sequential per turn (the agent doesn't fire a second tool call
        # before the first one's observation comes back).
        self._pending_tool: dict | None = None

        # The still-open, always-visible row for the current reasoning
        # burst (rendered like an agent reply -- see append_entry's
        # "thinking" branch for why). Reasoning streams in as many small
        # StreamingDeltaEvent batches (~1 every 40ms, see event_batcher.py)
        # -- each is only the new chunk since the last one, not the full
        # text so far. Without this, every batch would open its own
        # brand-new row instead of one row growing in place, which is what
        # actually looks like "every word gets its own row" during a long
        # reasoning burst.
        self._pending_thinking_row: dict | None = None

        # 2026-07-31: thinking + tool_call/tool_result no longer get their
        # own top-level timeline rows -- they're the noise a user doesn't
        # usually need to see step by step. Instead they collapse into one
        # "Working" card per turn (collapsed by default, click to expand the
        # full sequence), whose header text tracks what's happening right
        # now ("Thinking…", "Running terminal…"). Only the user's own
        # message and the agent's real reply stay as separate, always-
        # visible rows. A new user message or the agent's final answer ends
        # the current group; the next thinking/tool_call after that opens a
        # fresh one.
        self._active_group: dict | None = None

    # -- public API (unchanged signature from the previous bubble-log) -----

    def append_entry(
        self,
        kind: str,
        text: str,
        *,
        collapsed: bool = False,
        title: str | None = None,
        timestamp: datetime | None = None,
        meta: dict | None = None,
        code: str | None = None,
    ) -> None:
        # Not a real failure -- just the agent probing a host path inside its
        # own sandbox before it has actually connected that folder via
        # connect_folder (see workspace_server.py). Expected mid-workflow
        # noise, not something the user needs to see as a red alarm with a
        # Retry button (confirmed live 2026-07-31: this exact message shows
        # up right before the connect_folder confirmation dialog, every time).
        if kind == "error" and "is not a valid directory" in text.lower():
            kind = "system"

        label_prefix = title or kind
        self._entries.append(f"{label_prefix}: {text}" if kind != "user" else text)
        time_text = (timestamp or datetime.now()).strftime("%H:%M")

        if kind != "thinking":
            # Any other event ends the current reasoning burst -- the next
            # "thinking" after this starts a fresh row instead of resuming
            # a stale one from a previous turn.
            self._pending_thinking_row = None

        if kind == "tool_result" and self._pending_tool is not None:
            self._resolve_pending_tool("success", text, meta)
            return
        if kind == "error" and self._pending_tool is not None:
            self._resolve_pending_tool("error", text, meta)
            return

        if kind == "tool_call":
            self._group_new_tool_call(text, time_text, code=code)
        elif kind == "thinking":
            # 2026-08-01 reversal: this model's "thinking"/reasoning channel
            # is where it actually puts user-facing narration ("Looking for
            # X", "Found it: Y") per the app's own system-prompt
            # instructions -- confirmed live via screenshot that folding it
            # into the collapsed Working card (the 2026-07-31 design) made
            # exactly that narration easy to miss, the opposite of the
            # point of adding it. Now rendered as its own always-visible
            # row, styled like an agent reply, growing in place as more
            # reasoning streams in instead of one new row per chunk.
            self._append_thinking_as_agent_row(text, time_text)
        elif kind in ("user", "agent", "error"):
            # What was asked, the real answer, and a genuine error (LLM/infra
            # failures like a context-size overflow -- actionable, worth
            # seeing immediately) all get their own always-visible row.
            # Tool calls, system notices, and the benign "not a valid
            # directory" probe remapped to "system" above still fold into
            # the collapsed Working group.
            if self._active_group is not None:
                self._finalize_group(self._active_group)
            self._active_group = None
            self._pending_thinking_row = None
            self._new_plain_row(kind, text, time_text)
        else:
            # "system" notices (workspace confirmations, raw MCP tool
            # echoes) and tool_result/error with no pending call (an MCP
            # tool's own observation, a backfill edge case, or a result that
            # arrived after a history reload) -- all mid-turn noise, folded
            # into the group same as everything else.
            note = text if kind == "system" else f"{label_prefix}: {text}"
            self._group_new_note(note, time_text)

    def note_progress(self, text: str) -> None:
        """Updates the current Working card's header text -- the one part
        of it visible even while collapsed -- instead of adding a folded-in
        note nobody sees without expanding. For a long, fully-collapsed
        research stretch (2026-07-31: 30 read-only steps with no repeats,
        genuinely progressing) that would otherwise look completely
        stalled from the outside."""
        if self._active_group is not None:
            self._set_group_status(self._active_group, text)

    def clear(self) -> None:
        self._entries.clear()
        self._pending_tool = None
        self._pending_thinking_row = None
        self._active_group = None
        while self._layout.count() > 1:  # keep the trailing stretch
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def toPlainText(self) -> str:  # noqa: N802 -- matches QPlainTextEdit's API
        return "\n".join(self._entries)

    # -- row builders --------------------------------------------------------

    def _add_timeline_row(self, time_text: str, dot_color: str, card: QWidget) -> None:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(SPACE_XS)
        row_layout.addWidget(self._build_rail(time_text, dot_color))
        row_layout.addWidget(card, 1)
        self._layout.insertWidget(self._layout.count() - 1, row)

        # 2026-08-01: always follow the live edge now, even if the user had
        # scrolled up -- explicit request, reversing the previous
        # "only if already at bottom" behavior (which was itself an
        # earlier explicit request). New content should always be visible
        # immediately, especially across windows/tabs where the log was
        # last left scrolled somewhere else.
        self._scroll_to_bottom()

    def _build_rail(self, time_text: str, dot_color: str) -> QWidget:
        rail = QWidget()
        rail.setFixedWidth(_RAIL_WIDTH)
        rail.setStyleSheet("background: transparent;")
        outer = QHBoxLayout(rail)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(8)

        time_label = QLabel(time_text)
        time_label.setFixedWidth(38)
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        time_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        outer.addWidget(time_label)

        dot_col = QWidget()
        dot_col.setFixedWidth(8)
        dot_col.setStyleSheet("background: transparent;")
        dot_col_layout = QVBoxLayout(dot_col)
        dot_col_layout.setContentsMargins(0, 3, 0, 0)
        dot_col_layout.setSpacing(4)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setPixmap(_dot_pixmap(dot_color, 8))
        dot_col_layout.addWidget(dot)
        line = QFrame()
        line.setFixedWidth(2)
        line.setStyleSheet(f"background-color: {BORDER}; border: none;")
        dot_col_layout.addWidget(line, 1)
        outer.addWidget(dot_col)
        return rail

    def _new_card(self, border_color: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("TimelineCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        card.setStyleSheet(
            f"#TimelineCard {{ background-color: #101A2B; border: 1px solid {border_color}; "
            f"border-radius: {RADIUS_MD}px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE_SM, SPACE_XS + 2, SPACE_SM, SPACE_XS + 2)
        layout.setSpacing(SPACE_XXS)
        return card, layout

    def _header_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_XS)
        return row

    def _body_label(self, text: str, *, monospace: bool = False, color: str = TEXT_MUTED) -> QLabel:
        label = QLabel(_with_soft_wrap_points(text))
        label.setProperty("raw_text", text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        family = "font-family: 'JetBrains Mono', 'Fira Code', monospace;" if monospace else ""
        label.setStyleSheet(f"background: transparent; font-size: 13px; color: {color}; {family}")
        return label

    def _make_toggle(self) -> QToolButton:
        button = QToolButton()
        button.setCheckable(True)
        button.setChecked(False)
        button.setArrowType(Qt.ArrowType.RightArrow)
        button.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 0px; }"
        )
        return button

    # -- kind-specific cards --------------------------------------------------

    def _new_plain_row(self, kind: str, text: str, time_text: str) -> None:
        border_color, header_label, header_color = _CARD_STYLE.get(kind, _CARD_STYLE["system"])
        card, layout = self._new_card(border_color)

        if header_label:
            header = self._header_row()
            avatar = QLabel()
            avatar_tile = "user" if kind == "user" else "terminal"
            avatar.setPixmap(tile_icon(avatar_tile, 22).pixmap(22, 22))
            header.addWidget(avatar)
            name = QLabel(header_label)
            name.setStyleSheet(f"color: {header_color}; font-weight: 700; font-size: 13px; background: transparent;")
            header.addWidget(name)
            header.addStretch(1)
            # Copy on both -- a final answer worth reading is worth copying,
            # and an error's text is exactly the "diagnostics" a bug report
            # needs. Retry only makes sense on an error: it resends the last
            # message, which isn't a meaningful action on the agent's own
            # reply or the user's own echoed message.
            if kind in ("agent", "error"):
                header.addWidget(self._copy_button(text))
            if kind == "error":
                retry_btn = QToolButton()
                retry_btn.setText("Retry")
                retry_btn.setToolTip("Resend the last message")
                retry_btn.setStyleSheet(
                    f"QToolButton {{ color: {COLOR_DANGER}; font-weight: 600; font-size: 12px; "
                    "background: transparent; border: none; padding: 0px 4px; }}"
                )
                retry_btn.clicked.connect(self.retry_requested.emit)
                header.addWidget(retry_btn)
            layout.addLayout(header)

        # Long agent/system text (2026-07-31: seen in practice when the model
        # echoes a raw MCP tool result verbatim into its own answer) floods
        # the log just like an un-collapsed tool call used to -- same fix:
        # collapse by default with a one-line preview, click to expand.
        # user/error stay always-expanded: what you typed and what broke are
        # both worth seeing without an extra click.
        if kind in ("agent", "system") and len(text) > _PLAIN_ROW_COLLAPSE_THRESHOLD:
            preview = text.splitlines()[0][:120]
            toggle = self._make_toggle()
            toggle.setText(preview + ("…" if len(text) > len(preview) else ""))
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setStyleSheet(
                f"QToolButton {{ color: {TEXT_MUTED if kind == 'system' else TEXT_PRIMARY}; "
                "font-size: 13px; background: transparent; border: none; padding: 0px; }}"
            )
            layout.addWidget(toggle)
            body = self._body_label(text, color=(TEXT_MUTED if kind == "system" else TEXT_PRIMARY))
            body.setVisible(False)
            layout.addWidget(body)
            toggle.toggled.connect(
                lambda checked, b=toggle, w=body: (
                    b.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow),
                    w.setVisible(checked),
                )
            )
        else:
            body = self._body_label(text, color=(TEXT_MUTED if kind == "system" else TEXT_PRIMARY))
            layout.addWidget(body)

        self._add_timeline_row(time_text, border_color, card)

    def _copy_button(self, text: str) -> QToolButton:
        button = QToolButton()
        button.setText("Copy")
        button.setToolTip("Copy to clipboard")
        button.setStyleSheet(
            f"QToolButton {{ color: {TEXT_SECONDARY}; font-weight: 600; font-size: 12px; "
            "background: transparent; border: none; padding: 0px 4px; }"
        )
        button.clicked.connect(lambda: QApplication.clipboard().setText(text))
        return button

    def _ensure_activity_group(self, time_text: str) -> dict:
        """The one collapsed "Working" card a turn's thinking/tool_call
        events land in -- created on first use, reused until a plain row
        (user/agent/error/system) closes it. See _active_group's docstring
        in __init__ for why this exists."""
        if self._active_group is not None:
            return self._active_group
        card, layout = self._new_card(COLOR_THINKING_BORDER)
        card.setStyleSheet(
            f"#TimelineCard {{ background-color: {COLOR_THINKING_BG}; "
            f"border: 1px solid {COLOR_THINKING_BORDER}; border-radius: {RADIUS_MD}px; }}"
        )

        header = self._header_row()
        icon_label = QLabel()
        icon_label.setPixmap(thinking_icon(16).pixmap(16, 16))
        header.addWidget(icon_label)
        status_label = QLabel("Working…")
        status_label.setStyleSheet(
            f"color: {COLOR_THINKING_ACCENT}; font-weight: 700; font-size: 13px; background: transparent;"
        )
        header.addWidget(status_label)
        header.addStretch(1)
        elapsed_label = QLabel("0.0 s")
        elapsed_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
        header.addWidget(elapsed_label)
        toggle = self._make_toggle()
        toggle.setToolTip("Show thinking and tool calls for this turn")
        header.addWidget(toggle)
        layout.addLayout(header)

        # Live 1-2 line preview of the current reasoning text, visible even
        # while the card is collapsed (borrowed from LM Studio's own
        # collapsed-reasoning preview) -- without it, a long thinking burst
        # behind a collapsed card looked completely idle even while
        # streaming. Hidden once expanded since the full text/tool history
        # below already shows everything this was a preview of.
        preview_label = QLabel("")
        preview_label.setWordWrap(True)
        preview_label.setMaximumHeight(38)
        preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        preview_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12.5px; background: transparent;")
        preview_label.setVisible(False)
        layout.addWidget(preview_label)

        body_container = QWidget()
        body_container.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body_container)
        body_layout.setContentsMargins(0, SPACE_XS, 0, 0)
        body_layout.setSpacing(SPACE_XS)
        body_container.setVisible(False)
        layout.addWidget(body_container)
        toggle.toggled.connect(
            lambda checked, b=toggle, w=body_container, p=preview_label: (
                b.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow),
                w.setVisible(checked),
                p.setVisible(not checked and bool(p.text())),
            )
        )

        self._add_timeline_row(time_text, COLOR_THINKING_ACCENT, card)
        self._active_group = {
            "status_label": status_label,
            "body_layout": body_layout,
            "toggle": toggle,
            "elapsed_label": elapsed_label,
            "preview_label": preview_label,
            "start_time": datetime.now(),
            "completed": False,
        }
        self._start_group_timer()
        return self._active_group

    def _start_group_timer(self) -> None:
        if getattr(self, "_group_timer", None) is None:
            self._group_timer = QTimer(self)
            self._group_timer.setInterval(200)
            self._group_timer.timeout.connect(self._tick_group_timer)
        self._group_timer.start()

    def _tick_group_timer(self) -> None:
        group = self._active_group
        if group is None or group.get("completed"):
            self._group_timer.stop()
            return
        elapsed = (datetime.now() - group["start_time"]).total_seconds()
        group["elapsed_label"].setText(f"{elapsed:.1f} s")

    def _finalize_group(self, group: dict) -> None:
        """Called right before a group is closed (a new user/agent/error row
        is about to start) -- freezes the elapsed timer and flips the
        header to a completed state, matching how it looked while still
        running (LM Studio does the same: "Thinking… 12.4s" while live,
        "Completed" once done)."""
        if group.get("completed"):
            return
        group["completed"] = True
        elapsed = (datetime.now() - group["start_time"]).total_seconds()
        group["elapsed_label"].setText(f"{elapsed:.1f} s")
        self._set_group_status(group, "Completed ✓", COLOR_SUCCESS)

    def _group_content_added(self, group: dict) -> None:
        """New content inside an already-placed Working card doesn't change
        _add_timeline_row's row count, so it needs its own explicit
        follow-to-bottom call. Only matters while the card is actually
        expanded; collapsed growth doesn't change the visible height."""
        if group["toggle"].isChecked():
            self._scroll_to_bottom()

    def _group_sub_card(self, border_color: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("GroupSubCard")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        frame.setStyleSheet(
            f"#GroupSubCard {{ background-color: rgba(255, 255, 255, 10); "
            f"border: 1px solid {border_color}; border-radius: {RADIUS_MD}px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(SPACE_SM, SPACE_XXS, SPACE_SM, SPACE_XXS)
        layout.setSpacing(SPACE_XXS)
        return frame, layout

    def _set_group_status(self, group: dict, text: str, color: str = COLOR_THINKING_ACCENT) -> None:
        group["status_label"].setText(text)
        group["status_label"].setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 13px; background: transparent;"
        )

    def _append_thinking_as_agent_row(self, text: str, time_text: str) -> None:
        """Reasoning streams in as many small StreamingDeltaEvent batches
        (~1 every 40ms) -- grows the SAME row in place rather than opening a
        new one per chunk, same reasoning as the old group-based version
        this replaced."""
        if self._pending_thinking_row is not None:
            pending = self._pending_thinking_row
            combined = pending["text"] + text
            pending["text"] = combined
            pending["body"].setProperty("raw_text", combined)
            pending["body"].setText(_with_soft_wrap_points(combined))
            self._plain_row_content_grew()
            return
        border_color, header_label, header_color = _CARD_STYLE["agent"]
        card, layout = self._new_card(border_color)
        header = self._header_row()
        avatar = QLabel()
        avatar.setPixmap(tile_icon("terminal", 22).pixmap(22, 22))
        header.addWidget(avatar)
        name = QLabel(header_label)
        name.setStyleSheet(f"color: {header_color}; font-weight: 700; font-size: 13px; background: transparent;")
        header.addWidget(name)
        header.addStretch(1)
        layout.addLayout(header)
        body = self._body_label(text, color=TEXT_PRIMARY)
        layout.addWidget(body)
        self._add_timeline_row(time_text, border_color, card)
        self._pending_thinking_row = {"body": body, "text": text}

    def _plain_row_content_grew(self) -> None:
        """_add_timeline_row's scroll-to-bottom only runs when a new row is
        inserted -- growing an existing row's text (streaming reasoning
        into the same bubble) doesn't change the row count, so it needs its
        own explicit follow-to-bottom call."""
        self._scroll_to_bottom()

    def _group_new_tool_call(self, tool_name: str, time_text: str, *, code: str | None = None) -> None:
        group = self._ensure_activity_group(time_text)
        self._set_group_status(group, f"Running {tool_name}…")
        # tool_call_icon's own color choice doubles as the sub-card's
        # accent -- communicates *what kind* of call this is, status stays
        # the separate pill, never the card's own fill.
        accent = tool_accent_color(tool_name)
        frame, layout = self._group_sub_card(accent)

        header = self._header_row()
        icon_label = QLabel()
        icon_label.setPixmap(tool_call_icon(tool_name, 14).pixmap(14, 14))
        header.addWidget(icon_label)
        name = QLabel(tool_name)
        name.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 12px; background: transparent;"
        )
        header.addWidget(name)
        header.addStretch(1)
        pill = _StatusPill("running")
        header.addWidget(pill)
        layout.addLayout(header)

        meta_container = QWidget()
        meta_container.setStyleSheet("background: transparent;")
        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(0, 4, 0, 2)
        meta_layout.setSpacing(2)
        meta_container.setVisible(False)
        layout.addWidget(meta_container)

        body = self._body_label("", monospace=True)
        body.setVisible(False)
        layout.addWidget(body)

        group["body_layout"].addWidget(frame)
        self._pending_tool = {
            "pill": pill, "body": body, "meta_container": meta_container, "code": code, "group": group,
        }
        self._group_content_added(group)

    def _group_new_note(
        self, text: str, time_text: str, *, status: str | None = None, color: str = TEXT_MUTED
    ) -> None:
        """Everything that isn't thinking, a tool call, or Q&A -- system
        notices, raw MCP echoes, top-level errors with no pending tool call.
        A plain line inside the Working group rather than its own sub-card:
        there's no structured content here worth a header/toggle of its own."""
        group = self._ensure_activity_group(time_text)
        if status is not None:
            self._set_group_status(group, status, color)
        label = self._body_label(text, color=color)
        label.setStyleSheet(f"background: transparent; font-size: 12px; color: {color};")
        group["body_layout"].addWidget(label)
        self._group_content_added(group)

    def _resolve_pending_tool(self, status: str, text: str, meta: dict | None = None) -> None:
        pending = self._pending_tool
        self._pending_tool = None
        if pending is None:
            return
        pending["pill"].set_status(status)
        # Already sitting inside the collapsed Working group, so no second
        # layer of collapsing here -- what ran and what it returned are just
        # shown once the result lands.
        code = pending.get("code")
        combined = f"{code}\n\n--- result ---\n{text}" if code else text
        pending["body"].setProperty("raw_text", combined)
        pending["body"].setText(_with_soft_wrap_points(combined))
        color = COLOR_DANGER if status == "error" else TEXT_MUTED
        pending["body"].setStyleSheet(
            "background: transparent; font-size: 13px; "
            f"color: {color}; font-family: 'JetBrains Mono', 'Fira Code', monospace;"
        )
        pending["body"].setVisible(True)
        if meta and any(meta.get(k) not in (None, "") for k in ("command", "working_dir", "exit_code")):
            self._populate_meta(pending["meta_container"], meta)
            pending["meta_container"].setVisible(True)
        group = pending.get("group")
        if group is not None:
            self._set_group_status(group, "Working…")
            self._group_content_added(group)

    def _populate_meta(self, container: QWidget, meta: dict) -> None:
        """Structured Command/Working directory/Exit code/Duration block --
        every value here is read straight off the real TerminalObservation
        JSON (verified live against a running agent-server, 2026-07-30), or
        computed from two of that event's own real timestamps (duration).
        Nothing here is a guess at a field the server might send."""
        layout = container.layout()
        command = meta.get("command")
        if command:
            command_row = QHBoxLayout()
            command_row.setContentsMargins(0, 0, 0, 0)
            command_row.setSpacing(SPACE_XS)
            check_icon = QLabel()
            check_icon.setPixmap(_check_circle_pixmap(16))
            check_icon.setFixedSize(16, 16)
            command_row.addWidget(check_icon)
            command_label = QLabel(f"Command: {command}")
            command_label.setWordWrap(True)
            command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            command_label.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 12px; background: transparent; "
                "font-family: 'JetBrains Mono', 'Fira Code', monospace;"
            )
            command_row.addWidget(command_label, 1)
            layout.addLayout(command_row)

        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(0, 0, 0, 0)
        working_dir = meta.get("working_dir")
        if working_dir:
            wd_label = QLabel(f"Working directory: {working_dir}")
            wd_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
            detail_row.addWidget(wd_label)
        detail_row.addStretch(1)
        exit_code = meta.get("exit_code")
        if exit_code is not None:
            exit_color = COLOR_SUCCESS if exit_code == 0 else COLOR_DANGER
            exit_label = QLabel(f"Exit code: {exit_code}")
            exit_label.setStyleSheet(
                f"color: {exit_color}; font-size: 11px; font-weight: 600; background: transparent;"
            )
            detail_row.addWidget(exit_label)
        duration_s = meta.get("duration_s")
        if duration_s is not None:
            duration_label = QLabel(f"{duration_s:.1f}s")
            duration_label.setContentsMargins(SPACE_XS, 0, 0, 0)
            duration_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
            detail_row.addWidget(duration_label)
        layout.addLayout(detail_row)
        # Stays hidden until the user clicks the chevron -- the toggle
        # handler is what actually shows it (this only fills content in).

    def _scroll_to_bottom(self) -> None:
        # bar.maximum() right after insertWidget() still reflects the layout
        # from *before* this row -- Qt hasn't recomputed geometry yet, so
        # this landed short of the real bottom whenever a card was tall
        # (multi-line agent replies, tool output). Deferring one event-loop
        # tick lets the layout pass happen first.
        QTimer.singleShot(0, self._scroll_to_bottom_now)

    def _scroll_to_bottom_now(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _update_scroll_to_bottom_btn(self) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - self._AT_BOTTOM_TOLERANCE_PX
        # Nothing to scroll to yet on an empty/short log (maximum 0) either.
        self._scroll_to_bottom_btn.setVisible(not at_bottom and bar.maximum() > 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().resizeEvent(event)
        widget = self.widget()
        if widget is not None:
            widget.setMinimumWidth(self.viewport().width())
            widget.updateGeometry()
            if widget.layout() is not None:
                widget.layout().invalidate()
        margin = 14
        self._scroll_to_bottom_btn.move(
            self.width() - self._scroll_to_bottom_btn.width() - margin,
            self.height() - self._scroll_to_bottom_btn.height() - margin,
        )
