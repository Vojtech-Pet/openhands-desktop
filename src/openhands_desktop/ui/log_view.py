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

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.ui.icons import thinking_icon, tile_icon, tool_accent_color, tool_call_icon
from openhands_desktop.ui.palette import (
    BORDER,
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


def _check_mark_pixmap(size: int = 13, color: str = "#3FE0A6") -> QPixmap:
    """Bare checkmark stroke, no circle/box around it -- for the Completed
    pill, matching thinking-card.svg's plain check path exactly. A drawn
    pixmap rather than a "✓" text glyph: some fallback fonts render that
    character with its own visible box, which is the boxed look this
    replaces."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = painter.pen()
    pen.setColor(QColor(color))
    pen.setWidthF(size * 0.16)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline([
        QPointF(size * 0.06, size * 0.52),
        QPointF(size * 0.38, size * 0.82),
        QPointF(size * 0.96, size * 0.18),
    ])
    painter.end()
    return pixmap


# kind -> (card border color, header label, header text color)
_CARD_STYLE = {
    "user": (BORDER, "You", TEXT_PRIMARY),
    "agent": (COLOR_SUCCESS, "Agent", TEXT_PRIMARY),
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


def _completed_pill() -> QFrame:
    """The Thinking card's own status treatment -- a full rounded pill with
    a checkmark, not the plain dot+text _StatusPill tool-call cards use.
    Colors/layout match thinking-card.svg exactly (2026-07-30 reference)."""
    pill = QFrame()
    pill.setObjectName("CompletedPill")
    pill.setStyleSheet(
        "#CompletedPill { background-color: #0B2E35; border: 1px solid #1D665E; border-radius: 17px; }"
    )
    layout = QHBoxLayout(pill)
    layout.setContentsMargins(14, 6, 12, 6)
    layout.setSpacing(6)
    text = QLabel("Completed")
    text.setStyleSheet("color: #C9D6E2; font-size: 13px; background: transparent; border: none;")
    layout.addWidget(text)
    check = QLabel()
    check.setPixmap(_check_mark_pixmap(13))
    check.setStyleSheet("background: transparent; border: none;")
    layout.addWidget(check)
    return pill


class LogView(QScrollArea):
    # Emitted when the user clicks Retry on an error card -- the view has
    # no controller reference itself, so the owner (MainWindow) is the one
    # that actually resends the last message.
    retry_requested = Signal()

    _AT_BOTTOM_TOLERANCE_PX = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        self._layout.setSpacing(SPACE_MD)
        self._layout.addStretch(1)
        self.setWidget(container)

        self._entries: list[str] = []  # plain-text mirror, for tests/back-compat

        # The most recently opened tool_call card, waiting for its matching
        # tool_result/error observation -- None once resolved. A single
        # in-flight slot is correct for this client: OpenHands actions are
        # sequential per turn (the agent doesn't fire a second tool call
        # before the first one's observation comes back).
        self._pending_tool: dict | None = None

        # The still-open Thinking card for the current reasoning burst.
        # Reasoning streams in as many small StreamingDeltaEvent batches
        # (~1 every 40ms, see event_batcher.py) -- each is only the new
        # chunk since the last one, not the full text so far. Without this,
        # every batch would open its own brand-new Thinking card instead of
        # one card growing in place, which is what actually looks like
        # "every word gets its own card" during a long reasoning burst.
        self._pending_thinking: dict | None = None

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
        label_prefix = title or kind
        self._entries.append(f"{label_prefix}: {text}" if kind != "user" else text)
        time_text = (timestamp or datetime.now()).strftime("%H:%M")

        if kind != "thinking":
            # Any other event ends the current reasoning burst -- the next
            # "thinking" after this starts a fresh card instead of resuming
            # a stale one from a previous turn.
            self._pending_thinking = None

        if kind == "tool_result" and self._pending_tool is not None:
            self._resolve_pending_tool("success", text, meta)
            return
        if kind == "error" and self._pending_tool is not None:
            self._resolve_pending_tool("error", text, meta)
            return

        if kind == "tool_call":
            self._new_tool_call_row(text, time_text, code=code)
        elif kind == "thinking":
            if self._pending_thinking is not None:
                self._append_thinking_chunk(text)
            else:
                self._new_thinking_row(text, time_text)
        elif kind in ("user", "agent", "error", "system"):
            self._new_plain_row(kind, text, time_text)
        else:
            # tool_result/error with no pending call (backfill edge case,
            # or a result that arrived after a history reload) -- still
            # show it rather than silently dropping the content.
            self._new_plain_row("system", f"{label_prefix}: {text}", time_text)

    def clear(self) -> None:
        self._entries.clear()
        self._pending_tool = None
        while self._layout.count() > 1:  # keep the trailing stretch
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def toPlainText(self) -> str:  # noqa: N802 -- matches QPlainTextEdit's API
        return "\n".join(self._entries)

    # -- row builders --------------------------------------------------------

    def _add_timeline_row(self, time_text: str, dot_color: str, card: QWidget) -> None:
        bar = self.verticalScrollBar()
        # Read *before* inserting: appending a row raises bar.maximum(), so
        # this has to reflect where the user actually was a moment ago, not
        # where "bottom" ends up after the new content lands.
        was_at_bottom = bar.value() >= bar.maximum() - self._AT_BOTTOM_TOLERANCE_PX

        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(SPACE_XS)
        row_layout.addWidget(self._build_rail(time_text, dot_color))
        row_layout.addWidget(card, 1)
        self._layout.insertWidget(self._layout.count() - 1, row)

        # Only follow the stream if the user was already reading the live
        # edge -- scrolled up to reread an earlier step must not get yanked
        # back down by the next incoming event.
        if was_at_bottom:
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
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
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

    def _new_thinking_row(self, text: str, time_text: str) -> None:
        card, layout = self._new_card(COLOR_THINKING_BORDER)
        card.setStyleSheet(
            f"#TimelineCard {{ background-color: {COLOR_THINKING_BG}; "
            f"border: 1px solid {COLOR_THINKING_BORDER}; border-radius: {RADIUS_MD}px; }}"
        )

        header = self._header_row()
        icon_label = QLabel()
        icon_label.setPixmap(thinking_icon(16).pixmap(16, 16))
        header.addWidget(icon_label)
        name = QLabel("Thinking")
        name.setStyleSheet(
            f"color: {COLOR_THINKING_ACCENT}; font-weight: 700; font-size: 13px; background: transparent;"
        )
        header.addWidget(name)
        header.addStretch(1)
        # Reasoning is streamed in and read back after the fact -- by the
        # time it's visible in the log the chunk is already settled, so
        # "Completed" is accurate here (there's no separate "reasoning
        # just started" signal from the server to justify a Running state).
        header.addWidget(_completed_pill())
        toggle = self._make_toggle()
        header.addWidget(toggle)
        layout.addLayout(header)

        body = self._body_label(text, color=COLOR_THINKING_TEXT)
        body.setVisible(False)
        layout.addWidget(body)
        toggle.toggled.connect(
            lambda checked, b=toggle, w=body: (
                b.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow),
                w.setVisible(checked),
            )
        )

        self._add_timeline_row(time_text, COLOR_THINKING_ACCENT, card)
        self._pending_thinking = {"body": body, "text": text}

    def _append_thinking_chunk(self, text: str) -> None:
        pending = self._pending_thinking
        combined = pending["text"] + text
        pending["text"] = combined
        pending["body"].setProperty("raw_text", combined)
        pending["body"].setText(_with_soft_wrap_points(combined))

    def _new_tool_call_row(self, tool_name: str, time_text: str, *, code: str | None = None) -> None:
        icon_qicon = tool_call_icon(tool_name, 20)
        # tool_call_icon's own color choice doubles as the card's accent --
        # the card's color communicates *what kind* of call this is, status
        # is the separate pill, never the card's own fill.
        accent = tool_accent_color(tool_name)
        card, layout = self._new_card(accent)

        header = self._header_row()
        icon_label = QLabel()
        icon_label.setPixmap(icon_qicon.pixmap(20, 20))
        header.addWidget(icon_label)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        caption = QLabel("Tool call")
        caption.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        titles.addWidget(caption)
        name = QLabel(tool_name)
        name.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 14px; background: transparent;"
        )
        titles.addWidget(name)
        header.addLayout(titles)
        header.addStretch(1)

        pill = _StatusPill("running")
        header.addWidget(pill)
        toggle = self._make_toggle()
        toggle.setEnabled(False)  # nothing to expand until the result arrives
        header.addWidget(toggle)
        layout.addLayout(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background-color: {BORDER}; max-height: 1px; border: none;")
        divider.setVisible(False)
        layout.addWidget(divider)

        meta_container = QWidget()
        meta_container.setStyleSheet("background: transparent;")
        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(0, 6, 0, 4)
        meta_layout.setSpacing(2)
        meta_container.setVisible(False)
        layout.addWidget(meta_container)

        body = self._body_label("", monospace=True)
        body.setVisible(False)
        layout.addWidget(body)
        toggle.toggled.connect(
            lambda checked, b=toggle, w=body, m=meta_container, d=divider: (
                b.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow),
                w.setVisible(checked),
                m.setVisible(checked and m.layout().count() > 0),
                d.setVisible(checked),
            )
        )

        self._add_timeline_row(time_text, accent, card)
        self._pending_tool = {
            "pill": pill, "body": body, "toggle": toggle, "meta_container": meta_container, "code": code,
        }

    def _resolve_pending_tool(self, status: str, text: str, meta: dict | None = None) -> None:
        pending = self._pending_tool
        self._pending_tool = None
        if pending is None:
            return
        pending["pill"].set_status(status)
        # Shown together once expanded: what was actually run/written (the
        # action's own code/arguments -- collapsed by default, same as
        # Thinking, so a long file write doesn't flood the log), then its
        # result, so a click answers both "what did it do" and "what came
        # back" in one place.
        code = pending.get("code")
        combined = f"{code}\n\n--- result ---\n{text}" if code else text
        pending["body"].setProperty("raw_text", combined)
        pending["body"].setText(_with_soft_wrap_points(combined))
        color = COLOR_DANGER if status == "error" else TEXT_MUTED
        pending["body"].setStyleSheet(
            "background: transparent; font-size: 13px; "
            f"color: {color}; font-family: 'JetBrains Mono', 'Fira Code', monospace;"
        )
        if meta and any(meta.get(k) not in (None, "") for k in ("command", "working_dir", "exit_code")):
            self._populate_meta(pending["meta_container"], meta)
        pending["toggle"].setEnabled(True)

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
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
