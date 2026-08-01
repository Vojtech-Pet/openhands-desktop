from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QModelIndex, QRectF, QSize, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.models import resolve_conversation_family
from openhands_desktop.api.llm_server_client import (
    DEFAULT_LM_STUDIO_CONTEXT_LENGTH,
    classify_plan_or_code,
    detect_loaded_model,
    ensure_profile_model_loaded,
    probe_llm_server_state,
    profile_base_url,
    same_server,
)
from openhands_desktop.core.completion import RunState
from openhands_desktop.core.conversation_controller import ConversationController
from openhands_desktop.core.events import EventKind, NormalizedEvent
from openhands_desktop.core.history_store import HistoryStore
from openhands_desktop.core.ask_user_server import AskUserServer
from openhands_desktop.core.workspace_server import WorkspaceFolderServer
from openhands_desktop.core.preset_store import Preset, PresetStore
from openhands_desktop.core.workspace_preflight import run_preflight
from openhands_desktop.ui.ask_user_dialog import AskUserDialog
from openhands_desktop.ui.diff_view import ChangesDialog
from openhands_desktop.ui.icons import icon
from openhands_desktop.ui.task_board import TaskBoardDialog
from openhands_desktop.ui.icon_rail import IconRail
from openhands_desktop.ui.log_view import ConversationLogView
from openhands_desktop.ui.palette import (
    BG_BASE,
    BG_SURFACE_1,
    BG_SURFACE_2,
    BG_SURFACE_3,
    BORDER,
    BORDER_HOVER,
    COLOR_DANGER,
    COLOR_NEUTRAL,
    COLOR_PRIMARY,
    COLOR_THINKING_BG,
    COLOR_THINKING_BORDER,
    COLOR_WARNING,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from openhands_desktop.ui.right_panel import RightPanel, panel_toggle_button
from openhands_desktop.ui.settings_dialog import SettingsDialog
from openhands_desktop.ui.sidebar import Sidebar
from openhands_desktop.supervised_agent.openhands_supervisor import ConversationWatchdog
from openhands_desktop.ui.supervised_agent_dialog import SupervisedAgentDialog
from openhands_desktop.ui.spacing import (
    SPACE_MD,
    SPACE_SM,
    SPACE_XS,
    STATUS_BAR_HEIGHT,
    TOOLBAR_CONTROL_HEIGHT,
    TOOLBAR_HEIGHT,
)
from openhands_desktop.ui.theme import DARK_QSS
from openhands_desktop.ui.welcome_widget import WelcomeWidget


def _repolish(widget: QWidget) -> None:
    """Forces Qt to re-evaluate QSS #ObjectName rules after objectName
    changes -- just re-setting objectName does not repaint on its own."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _split_layout_icon(mode: str) -> QIcon:
    """Small workspace diagram used by the visual split-position menu."""
    pixmap = QPixmap(64, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    outer = QRectF(1.5, 1.5, 61, 37)
    painter.setPen(QPen(QColor(BORDER_HOVER), 1.5))
    painter.setBrush(QColor(BG_BASE))
    painter.drawRoundedRect(outer, 4, 4)

    content = QRectF(4, 4, 56, 32)
    if mode == "combined":
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(QColor(BG_SURFACE_2))
        painter.drawRoundedRect(content, 2.5, 2.5)
        painter.setPen(QPen(QColor(COLOR_PRIMARY), 1.5))
        painter.drawLine(10, 14, 48, 14)
        painter.setPen(QPen(QColor(TEXT_MUTED), 1.5))
        painter.drawLine(10, 21, 54, 21)
        painter.drawLine(10, 28, 40, 28)
    else:
        gap = 2.0
        if mode in ("left", "right"):
            activity_width = 18.0
            if mode == "left":
                activity = QRectF(content.left(), content.top(), activity_width, content.height())
                conversation = QRectF(activity.right() + gap, content.top(), content.width() - activity_width - gap, content.height())
            else:
                conversation = QRectF(content.left(), content.top(), content.width() - activity_width - gap, content.height())
                activity = QRectF(conversation.right() + gap, content.top(), activity_width, content.height())
        else:
            activity_height = 10.0
            if mode == "top":
                activity = QRectF(content.left(), content.top(), content.width(), activity_height)
                conversation = QRectF(content.left(), activity.bottom() + gap, content.width(), content.height() - activity_height - gap)
            else:
                conversation = QRectF(content.left(), content.top(), content.width(), content.height() - activity_height - gap)
                activity = QRectF(content.left(), conversation.bottom() + gap, content.width(), activity_height)
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(QColor(BG_SURFACE_2))
        painter.drawRoundedRect(conversation, 2, 2)
        painter.setPen(QPen(QColor(COLOR_THINKING_BORDER), 1))
        painter.setBrush(QColor(COLOR_THINKING_BG))
        painter.drawRoundedRect(activity, 2, 2)
    painter.end()
    return QIcon(pixmap)


def _format_action_code(tool_name: str | None, action: dict) -> str:
    """Renders an ActionEvent's raw `action` payload as readable code/text
    for the collapsed tool-call body -- same "collapsed by default, click to
    expand" treatment as Thinking, so a long file write or command doesn't
    flood the log by default. Field names (command/path/file_text/
    old_str/new_str/insert_line) confirmed live against real file_editor and
    terminal ActionEvents."""
    if tool_name == "terminal":
        return str(action.get("command", ""))
    if tool_name == "file_editor":
        command = action.get("command")
        path = action.get("path", "")
        if command == "create":
            return f"# create {path}\n{action.get('file_text', '')}"
        if command == "str_replace":
            return (
                f"# str_replace {path}\n--- old ---\n{action.get('old_str', '')}\n"
                f"--- new ---\n{action.get('new_str', '')}"
            )
        if command == "insert":
            return f"# insert into {path} at line {action.get('insert_line', '?')}\n{action.get('new_str', '')}"
        if command == "view":
            return f"# view {path}"
    return json.dumps(action, indent=2, ensure_ascii=False)


_HOST_PATH_RE = re.compile(
    r"(?:/home/[\w.-]+|/Users/[\w.-]+|/mnt/[\w.-]+|/root(?:/\S*)?|~/\S+|"
    r"[A-Za-z]:\\\S+)(?:/\S*)?"
)


def _find_host_path_reference(text: str) -> str | None:
    """First substring in `text` that looks like a path on the user's own
    machine (their home dir, a drive letter, /mnt, ...) rather than
    something meaningful inside the agent's sandbox. Deliberately narrow --
    only patterns that are near-certainly host-only, so this doesn't fire on
    ordinary sandbox-relative paths like /workspace/... or bare filenames."""
    match = _HOST_PATH_RE.search(text)
    return match.group(0) if match else None


class ClickableChip(QWidget):
    """A #TopBarChip-styled container that emits `clicked` -- used where a
    chip needs custom multi-line content (icon + caption + value) that a
    plain QPushButton can't lay out, but still needs to act as a button."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBarChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(TOOLBAR_CONTROL_HEIGHT)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 -- Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# Fallback limit used only until the server reports real token usage (see
# _context_usage). Mirrors agent_settings.condenser.max_tokens as configured
# on the server (2026-07-29: 150000, sitting below LM Studio's 196608 loaded
# context so condensation happens before old instructions get clipped out of
# the prompt). Once the first LLM call lands, the real per-conversation
# context_window from the server replaces this value entirely.
_CONDENSER_MAX_TOKENS = 150000

# Name this app registers itself under in agent_settings.mcp_config, and how
# long the agent is allowed to block on a question. It has to be generous --
# the call is waiting on a human reading options and clicking.
_ASK_USER_MCP_NAME = "openhands-desktop-ask-user"
_ASK_USER_TIMEOUT_S = 900
_WORKSPACE_MCP_NAME = "openhands-desktop-workspace"
# See MainWindow._on_state_changed: caps automatic interrupt+nudge attempts
# per run so a genuinely broken model loop still surfaces the manual button
# instead of nudging forever. Set to 1 (2026-07-30, user request): give the
# model exactly one automatic redirect, then stop and wait for the user --
# a repeat STUCK after the nudge already tried to fix it means the task
# should halt, not keep retrying the same failing pattern.
_MAX_AUTO_NUDGES_PER_RUN = 1

# See MainWindow._on_state_changed: a status poll and a real, actively
# streaming ActionEvent/MessageEvent/thinking-delta can race in either
# order -- the server's own execution_status briefly reads "finished" (or
# once, "error") between agent steps, a known OpenHands quirk, independent
# of whether the agent is actually still working. Confirmed live 2026-07-31
# a purely reactive fix (correct the label only when a new event arrives)
# wasn't enough: a slightly later poll using that same stale "finished"
# reading flipped the label right back within a couple seconds, and the
# duration timer stayed frozen the whole time. Any real event within this
# many seconds of "now" is treated as proof the conversation is still
# active, overriding a stale terminal reading regardless of which arrived
# first.
_RECENT_ACTIVITY_GRACE_S = 8

STATE_LABELS = {
    RunState.IDLE: ("Idle", "StateIdle"),
    RunState.RUNNING: ("Running…", "StateRunning"),
    RunState.PAUSED: ("Paused", "StateWarning"),
    RunState.WAITING_FOR_CONFIRMATION: ("Waiting for confirmation", "StateWarning"),
    # Its own style, not StateWarning: "finished but nobody confirmed it
    # actually succeeded" must never look like the same thing as "paused" or
    # blend into "completed" -- see the design spec's explicit call-out that
    # Completed and Finished-unverified must stay unambiguous at a glance.
    # Labeled "Waiting", not "Finished (unverified)" -- the model stopped
    # producing output without an explicit finish() call, which reads as
    # done-but-not-confirmed, not as a terminal state; "Waiting" makes clear
    # it's expecting the user to look and decide what happens next.
    RunState.FINISHED_UNVERIFIED: ("Waiting", "StateFinishedUnverified"),
    RunState.COMPLETED: ("Completed", "StateCompleted"),
    RunState.STUCK: ("Stuck", "StateError"),
    RunState.ERROR: ("Error", "StateError"),
    RunState.DELETING: ("Deleting…", "StateWarning"),
}


class _CurrentSelectionDelegate(QStyledItemDelegate):
    """Draws a green checkmark on the right side of whichever row is the
    combo's *current* selection, so it stays visible even while hovering
    other rows (hover highlight itself comes from theme.py's
    QAbstractItemView::item:hover rule) -- otherwise there's no way to tell,
    while the popup is open, which profile is actually active."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().paint(painter, option, index)
        if index.row() == self._combo.currentIndex():
            check = icon("tests-check", 14).pixmap(14, 14)
            x = option.rect.right() - check.width() - 8
            y = option.rect.center().y() - check.height() // 2
            painter.drawPixmap(x, y, check)


class _HoldStopButton(QPushButton):
    """Short press pauses; a five-second hold performs a hard interrupt."""

    pause_requested = Signal()
    stop_requested = Signal()
    HOLD_MS = 5000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._holding = False
        self._hard_stop_fired = False
        self._elapsed = QElapsedTimer()
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._complete_hold)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(40)
        self._progress_timer.timeout.connect(self.update)
        self.pressed.connect(self._start_hold)
        self.released.connect(self._release_hold)

    def _start_hold(self) -> None:
        self._holding = True
        self._hard_stop_fired = False
        self._elapsed.start()
        self._hold_timer.start(self.HOLD_MS)
        self._progress_timer.start()
        self.update()

    def _release_hold(self) -> None:
        if not self._holding:
            return
        should_pause = not self._hard_stop_fired
        self._holding = False
        self._hold_timer.stop()
        self._progress_timer.stop()
        self.update()
        if should_pause:
            self.pause_requested.emit()

    def _complete_hold(self) -> None:
        if not self._holding or self._hard_stop_fired:
            return
        self._hard_stop_fired = True
        self._progress_timer.stop()
        self.update()
        self.stop_requested.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().paintEvent(event)
        if not self._holding:
            return
        progress = min(1.0, self._elapsed.elapsed() / self.HOLD_MS)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = COLOR_DANGER if self._hard_stop_fired else COLOR_WARNING
        painter.setPen(QPen(QColor(color), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(self.rect().adjusted(2, 2, -2, -2), 90 * 16, int(-360 * 16 * progress))
        painter.end()


class _PresetManagerDialog(QDialog):
    """Lists saved presets with a Delete button per row -- the only
    management surface presets need beyond the Save/Apply menu itself."""

    delete_requested = Signal(str)  # preset name

    def __init__(self, parent: QWidget, presets: list[Preset]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage presets")
        self.resize(420, 320)
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        for preset in presets:
            model_part = f" · {preset.llm_profile_name}" if preset.llm_profile_name else ""
            item = QListWidgetItem(f"{preset.name}  ({preset.agent_type}{model_part})")
            item.setData(Qt.ItemDataRole.UserRole, preset.name)
            self._list.addItem(item)
        if not presets:
            empty_item = QListWidgetItem("No presets saved yet.")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty_item)
        layout.addWidget(self._list)

        delete_btn = QPushButton("Delete selected")
        delete_btn.clicked.connect(self._on_delete_clicked)
        layout.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignLeft)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_delete_clicked(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        row = self._list.row(item)
        self._list.takeItem(row)
        self.delete_requested.emit(name)


class ChatInputEdit(QPlainTextEdit):
    """Multi-line input: Ctrl+Enter sends and plain Enter adds a newline."""

    def __init__(self, on_send, on_focus_change=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_send = on_send
        self._on_focus_change = on_focus_change
        self.setPlaceholderText("What would you like the agent to do? (Ctrl+Enter to send)")
        self.setMinimumHeight(46)
        self.setMaximumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.document().contentsChanged.connect(self._fit_to_content)
        QTimer.singleShot(0, self._fit_to_content)

    def _fit_to_content(self) -> None:
        margins = self.contentsMargins()
        doc_height = int(self.document().size().height())
        frame = self.frameWidth() * 2
        padding = margins.top() + margins.bottom() + frame + 10
        target = max(self.minimumHeight(), min(self.maximumHeight(), doc_height + padding))
        if self.minimumHeight() != target:
            self.setMinimumHeight(target)
            self.updateGeometry()

    def focusInEvent(self, event) -> None:  # noqa: N802 -- Qt override
        if self._on_focus_change:
            self._on_focus_change(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 -- Qt override
        if self._on_focus_change:
            self._on_focus_change(False)
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 -- Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._on_send()
            return
        if event.key() == Qt.Key.Key_Escape:
            # Escape is commonly used to dismiss menus and focus. Pausing a
            # running agent here made accidental pauses look spontaneous.
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


_open_windows: list["MainWindow"] = []


class MainWindow(QMainWindow):
    def __init__(
        self,
        client: AppServerClient,
        history_store: HistoryStore | None = None,
        shared_ask_user_server: "AskUserServer | None" = None,
        shared_workspace_server: "WorkspaceFolderServer | None" = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("OpenHands Desktop")
        self.resize(1100, 720)
        self.setMinimumSize(860, 560)
        self.setStyleSheet(DARK_QSS)

        self._client = client
        self._settings = QSettings("OpenHandsDesktop", "MainWindow")
        self._ensure_default_model_settings()
        self._profiles_by_name: dict[str, object] = {}
        self._history = history_store or HistoryStore()
        # Serializes sidebar status writes: state_changed can fire in quick
        # succession (e.g. ERROR then a recovery back to RUNNING/COMPLETED),
        # each write opens its own DB connection with no ordering guarantee
        # across them, and a slower *earlier* write finishing after a faster
        # *later* one could silently leave the sidebar stuck on a stale
        # status (confirmed live 2026-07-31: an error the agent recovered
        # from left it frozen on a terminal status even though the agent
        # kept working and finished normally). The lock forces writes to
        # apply in dispatch order.
        self._history_write_lock = asyncio.Lock()
        self._sidebar_refresh_seq = 0
        self._presets = PresetStore()
        self._preset_list: list[Preset] = []
        self._pending_model: str | None = None
        self._pending_title: str | None = None
        self._current_agent_type: str | None = None
        # Tracks *which conversation* is a finished Plan waiting to hand off
        # to Code, independent of _current_agent_type -- confirmed live
        # 2026-07-31: with Auto-continue on, _continue_as_code_async sets
        # _current_agent_type = "default" for the *new* Code conversation
        # before the old Plan controller is actually torn down, so a late
        # state_changed still arriving for the old (still-attached) Plan
        # conversation read agent_type as "default" and showed plain
        # "Finished" instead of "Waiting to continue as Code". Keyed by
        # conversation_id so it survives that kind of mutation elsewhere.
        self._plan_waiting_conversation_id: str | None = None
        self._conversation_started_at: datetime | None = None
        self._duration_frozen = False
        self._auto_continue_triggered = False
        self._auto_supervise = self._settings.value("auto_supervise", True, type=bool)
        self._watchdog: ConversationWatchdog | None = None
        # Auto-nudge on RunState.STUCK (see _on_state_changed): capped per
        # run so a genuinely broken loop falls back to the manual button
        # after a few tries instead of auto-nudging forever.
        self._auto_nudge_count_for_run = 0
        self._auto_nudge_in_flight = False
        self._stuck_prompt_shown_for_run = False
        self._host_path_correction_sent_for_run = False
        self._silence_recovery_count_for_run = 0
        # Set by _interrupt() (the Stop button): interrupt() only cancels
        # the in-flight LLM call, it doesn't tell the agent WHY -- confirmed
        # live 2026-08-01 that a plain follow-up message sent right after
        # Stop was not treated as taking priority over the just-interrupted
        # task, the agent just picked the old task back up. The next _send()
        # after a Stop click wraps the user's text with an explicit
        # priority instruction instead of sending it verbatim.
        self._user_interrupted_awaiting_priority = False
        # See the priority-message block in _send(): whether the very next
        # agent action after a priority-wrapped message was a tool call
        # (meaning it ignored the "answer directly" instruction) rather
        # than a real text answer.
        self._priority_enforcement_pending = False
        self._priority_enforcement_sent = False
        self._last_run_state: RunState | None = None
        self._last_event_at: datetime | None = None
        self._state_generation = 0
        self._tool_call_count = 0
        self._estimated_context_chars = 0
        self._real_used_tokens = 0
        self._real_context_window = 0
        self._auto_compact_triggered = False
        # Set on a terminal ActionEvent, read back on its matching
        # ObservationEvent to compute a real duration (server doesn't report
        # one) -- see _render_event. Single slot mirrors LogView's own
        # single-pending-tool-call assumption (actions are sequential).
        self._pending_terminal_action_ts: datetime | None = None
        # True while a "task" (launch_subagent) call is in flight -- that
        # tool blocks until the sub-agent's own run finishes, which can
        # legitimately take far longer than the silence-detector's normal
        # timeout with zero events reaching this (the parent) conversation
        # in between. See _check_conversation_silence.
        self._pending_task_action = False
        self._recent_user_messages: list[str] = []
        self._is_resuming = False
        self._continuing_from_plan_id: str | None = None
        self._conversation_starting = False
        self._model_switching = False
        self._health_check_in_flight = False
        self._model_check_in_flight = False
        self._shutdown_complete = False
        self._shutdown_started = False
        self._controller: ConversationController | None = None
        self._settings_dialog: SettingsDialog | None = None
        # Bound to fixed ports (see main.py's single-instance guard note),
        # so a second window must reuse these, not construct its own --
        # otherwise it just fails to bind. Only the window that actually
        # created them ("owns" them) tears them down, and only once it's
        # the last window left open -- see _shutdown_before_close.
        #
        # KNOWN LIMITATION: the callbacks below are bound to *this*
        # window's handlers regardless of which window's conversation
        # actually triggered the MCP call, so an ask_user_question or
        # workspace-connect confirmation from a second window's
        # conversation currently pops up on the window that owns the
        # servers, not the one whose conversation is asking. Acceptable
        # for now (nothing is silently lost, just surfaces on the "wrong"
        # window) but worth revisiting if that proves confusing in practice.
        self._owns_mcp_servers = shared_ask_user_server is None
        self._ask_user_server = shared_ask_user_server or AskUserServer(self._on_agent_question)
        self._workspace_server = shared_workspace_server or WorkspaceFolderServer(
            on_connected=self._on_agent_connected_folder,
            on_confirm_connect=self._confirm_workspace_connect,
            on_confirm_write=self._confirm_workspace_write,
        )
        self._custom_instructions_text = ""
        self._error_history: list[tuple[datetime, str]] = []
        self._llm_server_state = "unreachable"
        _open_windows.append(self)

        self._restore_window_geometry()

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.icon_rail = IconRail()
        self.icon_rail.workspace_requested.connect(self._check_workspace)
        self.icon_rail.settings_requested.connect(self._open_settings)
        root_layout.addWidget(self.icon_rail)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("MainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.main_splitter, 1)

        self.sidebar = Sidebar()
        self.sidebar.new_conversation_requested.connect(self._start_fresh_conversation)
        self.sidebar.conversation_selected.connect(self._resume_conversation)
        self.sidebar.conversation_delete_requested.connect(self._confirm_delete_conversation)
        self.sidebar.settings_requested.connect(self._open_settings)
        self.main_splitter.addWidget(self.sidebar)

        main_area = QWidget()
        main_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.main_splitter.addWidget(main_area)

        self.right_panel = RightPanel()
        self.right_panel.setVisible(False)
        self.main_splitter.addWidget(self.right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self._restore_splitter_state()

        # --- top bar ---
        self.top_bar = QWidget()
        self.top_bar.setObjectName("TopBar")
        self.top_bar.setFixedHeight(TOOLBAR_HEIGHT)
        top_bar_layout = QHBoxLayout(self.top_bar)
        top_bar_layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        top_bar_layout.setSpacing(SPACE_XS)

        # Menu + logo live in the sidebar itself (top-left of the whole
        # window, per the target mockup) -- not repeated here.

        # Health chip
        self.health_icon_label = QLabel()
        self.health_icon_label.setPixmap(icon("health", 14, COLOR_NEUTRAL).pixmap(14, 14))
        self._health_full_text = "Health: unknown"
        self.health_label = QLabel(self._health_full_text)
        self.health_chip = self._simple_chip([self.health_icon_label, self.health_label])
        top_bar_layout.addWidget(self.health_chip)

        # Workspace chip (two-line caption/value, per the design spec) --
        # clicking it runs the permission preflight, it does NOT select a
        # different mount: OpenHands' API has no per-conversation workspace
        # selection (see local-findings/issue-07). The value line makes
        # that explicit instead of implying a working project switcher.
        self.workspace_chip = ClickableChip()
        self.workspace_chip.setToolTip(
            "Check a local directory for the uid/gid mismatch problems that cause "
            "'Permission denied' and git 'dubious ownership' once the sandbox "
            "(running as a different uid) writes to it.\n\n"
            "Note: this checks permissions, it does not select which project the "
            "server mounts -- OpenHands' API has no per-conversation workspace "
            "selection today (see local-findings/issue-07)."
        )
        self._workspace_full_text = "Check permissions…"
        self.workspace_value_label = QLabel(self._workspace_full_text)
        self._fill_two_line_chip(
            self.workspace_chip, "workspace", "Workspace", self.workspace_value_label
        )
        self.workspace_chip.clicked.connect(self._check_workspace)
        top_bar_layout.addWidget(self.workspace_chip)

        # Model chip: caption + a real QComboBox styled borderless as the
        # "value" line (this one IS a functional selector, unlike Workspace).
        self.model_chip = QWidget()
        self.model_chip.setObjectName("TopBarChip")
        self.model_chip.setFixedHeight(TOOLBAR_CONTROL_HEIGHT)
        self.model_chip.setMinimumWidth(210)
        self.model_chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        model_chip_row = QHBoxLayout(self.model_chip)
        model_chip_row.setContentsMargins(SPACE_XS, 3, SPACE_XS, 3)
        model_chip_row.setSpacing(SPACE_XS)
        model_icon_label = QLabel()
        model_icon_label.setPixmap(icon("model-ai", 18).pixmap(18, 18))
        model_chip_row.addWidget(model_icon_label)
        model_text_col = QVBoxLayout()
        model_text_col.setSpacing(0)
        self.model_caption = QLabel("Model")
        self.model_caption.setObjectName("ChipCaption")
        model_text_col.addWidget(self.model_caption)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ChipValue")
        self.model_combo.setFrame(False)
        self.model_combo.setMinimumWidth(112)
        self.model_combo.view().setMinimumWidth(360)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(18)
        self.model_combo.setStyleSheet(
            "QComboBox#ChipValue { background: transparent; border: none; padding: 0px; }"
            "QComboBox#ChipValue QAbstractItemView { min-width: 360px; }"
        )
        self.model_combo.setToolTip("LLM profile used for the next new conversation")
        self.model_combo.setItemDelegate(_CurrentSelectionDelegate(self.model_combo))
        self.model_combo.currentIndexChanged.connect(self._on_model_selection_changed)
        model_text_col.addWidget(self.model_combo)
        model_chip_row.addLayout(model_text_col, 1)
        self.unload_model_btn = QPushButton("Unload")
        self.unload_model_btn.setToolTip(
            "Unload the model currently loaded in LM Studio to free up VRAM. "
            "Only runs if no conversation is actively running."
        )
        self.unload_model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unload_model_btn.clicked.connect(self._on_unload_model_clicked)
        model_chip_row.addWidget(self.unload_model_btn)
        top_bar_layout.addWidget(self.model_chip)

        self.thinking_chip = QWidget()
        self.thinking_chip.setObjectName("TopbarToggleGroup")
        thinking_chip_layout = QHBoxLayout(self.thinking_chip)
        thinking_chip_layout.setContentsMargins(0, 0, 0, 0)
        thinking_chip_layout.setSpacing(0)
        self.enable_thinking_check = QPushButton()
        self.enable_thinking_check.setCheckable(True)
        self.enable_thinking_check.setText("Think")
        self.enable_thinking_check.setFixedHeight(28)
        self.enable_thinking_check.setMinimumWidth(62)
        self.enable_thinking_check.setObjectName("TopbarToggleButton")
        self.enable_thinking_check.setToolTip(
            "Thinking: enable model reasoning for the selected LLM profile. "
            "Applies to the next new conversation."
        )
        self.enable_thinking_check.toggled.connect(self._on_thinking_toggle_changed)
        thinking_chip_layout.addWidget(self.enable_thinking_check)
        top_bar_layout.addWidget(self.thinking_chip)

        self.keep_chip = QWidget()
        self.keep_chip.setObjectName("TopbarToggleGroup")
        keep_chip_layout = QHBoxLayout(self.keep_chip)
        keep_chip_layout.setContentsMargins(0, 0, 0, 0)
        keep_chip_layout.setSpacing(0)
        self.preserve_thinking_check = QPushButton()
        self.preserve_thinking_check.setCheckable(True)
        self.preserve_thinking_check.setText("Keep")
        self.preserve_thinking_check.setFixedHeight(28)
        self.preserve_thinking_check.setMinimumWidth(58)
        self.preserve_thinking_check.setObjectName("TopbarToggleButton")
        self.preserve_thinking_check.setToolTip(
            "Keep thinking: preserve previous thinking blocks across turns for the selected LLM profile. "
            "Applies to the next new conversation."
        )
        self.preserve_thinking_check.toggled.connect(self._on_thinking_toggle_changed)
        keep_chip_layout.addWidget(self.preserve_thinking_check)
        top_bar_layout.addWidget(self.keep_chip)
        self._update_thinking_toggle_icons()

        self.history_btn = QPushButton("History")
        self.history_btn.setIcon(icon("history"))
        self.history_btn.clicked.connect(self._refresh_sidebar_history)
        top_bar_layout.addWidget(self.history_btn)

        self.changes_btn = QPushButton("Changes")
        self.changes_btn.setIcon(icon("git-history"))
        self.changes_btn.setToolTip("Files touched by the agent this session, with a real diff per file")
        self.changes_btn.setEnabled(False)
        self.changes_btn.clicked.connect(self._open_changes)
        top_bar_layout.addWidget(self.changes_btn)

        self.browser_preview_btn = QPushButton("Browser")
        self.browser_preview_btn.setIcon(icon("browser"))
        self.browser_preview_btn.setToolTip(
            "Open a live view of the sandbox's browser (noVNC) in your system browser -- "
            "shows what browser_navigate/browser_get_state are actually doing"
        )
        self.browser_preview_btn.setEnabled(False)
        self.browser_preview_btn.clicked.connect(self._open_browser_preview)
        top_bar_layout.addWidget(self.browser_preview_btn)

        self.errors_btn = QPushButton("Errors")
        self.errors_btn.setIcon(icon("error"))
        self.errors_btn.setToolTip("Every error in this conversation, collected in one place")
        self.errors_btn.setEnabled(False)
        self.errors_btn.clicked.connect(self._open_errors_dialog)
        top_bar_layout.addWidget(self.errors_btn)

        self.tasks_btn = QPushButton("Tasks")
        self.tasks_btn.setIcon(icon("plan-tasks"))
        self.tasks_btn.setObjectName("TopbarMoreButton")
        self.tasks_btn.setToolTip("Mission Control -- every conversation on this server, live")
        self.tasks_btn.clicked.connect(self._open_task_board)
        top_bar_layout.addWidget(self.tasks_btn)

        self.supervised_agent_btn = QPushButton("Supervised Agent")
        self.supervised_agent_btn.setToolTip(
            "Run a task through the supervised control loop (STUCK/verifier/step limits) "
            "against a chosen workspace, talking directly to LM Studio -- independent of "
            "this conversation."
        )
        self.supervised_agent_btn.clicked.connect(self._open_supervised_agent)
        top_bar_layout.addWidget(self.supervised_agent_btn)

        top_bar_layout.addStretch()

        saved_split_position = str(self._settings.value("split_activity_position", "right"))
        if saved_split_position not in ("left", "right", "top", "bottom"):
            saved_split_position = "right"
        split_enabled = self._settings.value("split_activity_view", False, type=bool)
        self._split_view_mode = saved_split_position if split_enabled else "combined"
        self.split_view_btn = QToolButton()
        self.split_view_btn.setObjectName("TopbarMoreButton")
        self.split_view_btn.setText("Split")
        self.split_view_btn.setIcon(icon("panel", 16))
        self.split_view_btn.setCheckable(True)
        self.split_view_btn.setChecked(split_enabled)
        self.split_view_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.split_view_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.split_view_btn.setToolTip("Choose where the separate activity panel appears")
        self.split_view_menu = QMenu(self.split_view_btn)
        self._populate_split_view_menu(self.split_view_menu)
        self.split_view_btn.setMenu(self.split_view_menu)
        top_bar_layout.addWidget(self.split_view_btn)

        self.topbar_status_widget = QWidget()
        status_col = QVBoxLayout(self.topbar_status_widget)
        status_col.setContentsMargins(0, 0, 0, 0)
        status_col.setSpacing(0)
        status_top_row = QHBoxLayout()
        status_top_row.setSpacing(SPACE_XS)
        self.overall_status_icon_label = QLabel()
        self.overall_status_icon_label.setPixmap(icon("tests-check", 14, COLOR_NEUTRAL).pixmap(14, 14))
        self.overall_status_label = QLabel("Checking…")
        status_top_row.addWidget(self.overall_status_icon_label)
        status_top_row.addWidget(self.overall_status_label)
        status_col.addLayout(status_top_row)
        self.overall_status_sublabel = QLabel("")
        self.overall_status_sublabel.setObjectName("ChipCaption")
        status_col.addWidget(self.overall_status_sublabel)
        top_bar_layout.addWidget(self.topbar_status_widget)

        self.topbar_menu_btn = QPushButton()
        self.topbar_menu_btn.setObjectName("TopbarMoreButton")
        self.topbar_menu_btn.setIcon(icon("menu", 18))
        self.topbar_menu_btn.setText("More")
        self.topbar_menu_btn.setMinimumWidth(76)
        self.topbar_menu_btn.setToolTip("More toolbar actions")
        self.topbar_menu_btn.clicked.connect(self._show_topbar_menu)
        top_bar_layout.addWidget(self.topbar_menu_btn)

        self.panel_toggle_btn = panel_toggle_button()
        self.panel_toggle_btn.toggled.connect(self.right_panel.setVisible)
        top_bar_layout.addWidget(self.panel_toggle_btn)
        self._topbar_optional_widgets = (
            self.supervised_agent_btn,
            self.tasks_btn,
            self.errors_btn,
            self.browser_preview_btn,
            self.changes_btn,
            self.history_btn,
            self.unload_model_btn,
            self.workspace_chip,
            self.health_chip,
        )
        self._topbar_action_labels = {
            self.history_btn: "History",
            self.changes_btn: "Changes",
            self.browser_preview_btn: "Browser",
            self.errors_btn: "Errors",
            self.tasks_btn: "Tasks",
            self.supervised_agent_btn: "Supervised Agent",
        }
        QTimer.singleShot(0, self._update_topbar_compact)

        main_layout.addWidget(self.top_bar)

        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.content_splitter.setObjectName("ContentSplitter")
        self.content_splitter.setChildrenCollapsible(False)
        main_layout.addWidget(self.content_splitter, 1)

        # --- stacked content: welcome vs. live chat log ---
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.welcome = WelcomeWidget()
        self.welcome.suggestion_clicked.connect(self._on_suggestion_clicked)
        self.log = ConversationLogView()
        self.log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.log.retry_requested.connect(self._retry_last_message)
        if self._split_view_mode != "combined":
            self.log.set_split_position(self._split_view_mode)
        self.log.set_split_enabled(self._split_view_mode != "combined")
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.log)
        self.stack.setCurrentWidget(self.welcome)
        self.content_splitter.addWidget(self.stack)

        # --- composer ---
        # Single bar, per the message-composer.svg reference (2026-07-30):
        # attach icon inline with the input on top, agent-type pill + tool
        # buttons + Send/Stop all in one row underneath -- replacing the
        # previous two-column layout (composer frame + a separate Send/Stop
        # button stack beside it).
        input_row = QWidget()
        input_row.setMinimumHeight(118)
        input_row.setMaximumHeight(320)
        input_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        input_row_layout = QVBoxLayout(input_row)
        input_row_layout.setContentsMargins(SPACE_MD, SPACE_XS, SPACE_MD, SPACE_SM)
        input_row_layout.setSpacing(0)

        self.composer_frame = QFrame()
        composer_frame = self.composer_frame
        composer_frame.setObjectName("ComposerFrame")
        composer_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        composer_layout = QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        composer_layout.setSpacing(SPACE_XS)

        input_line = QHBoxLayout()
        input_line.setSpacing(SPACE_XS)
        self.attach_btn = QPushButton()
        self.attach_btn.setObjectName("ComposerToolButton")
        self.attach_btn.setIcon(icon("attach", 18))
        self.attach_btn.setToolTip("Insert a file path into the message")
        self.attach_btn.clicked.connect(self._insert_attachment_reference)
        input_line.addWidget(self.attach_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.input = ChatInputEdit(
            on_send=self._send, on_focus_change=self._on_composer_focus_change
        )
        self.input.setObjectName("ComposerInput")
        input_line.addWidget(self.input, 1)
        composer_layout.addLayout(input_line)

        composer_tools_row = QHBoxLayout()
        composer_tools_row.setSpacing(SPACE_XS)
        self.agent_type_combo = QComboBox()
        self.agent_type_combo.setObjectName("ComposerPill")
        self.agent_type_combo.setIconSize(QSize(15, 15))
        self.agent_type_combo.addItem(icon("code", 15), "Code", userData="default")
        self.agent_type_combo.addItem(
            icon("plan-tasks", 15), "Plan (read-only, no terminal)", userData="plan"
        )
        self.agent_type_combo.setToolTip(
            "Code: full read/write agent with a terminal.\n"
            "Plan: read-only (glob/grep/file-view only, no terminal) -- "
            "produces a plan, does not implement it."
        )
        self.agent_type_combo.currentIndexChanged.connect(self._on_agent_type_changed)
        composer_tools_row.addWidget(self.agent_type_combo)

        # Borrowed from Claude Code's permission-mode selector: one combo
        # that bundles confirmation_mode + security_analyzer into named
        # presets instead of requiring a trip through Settings ->
        # Verification to change either. Pushes straight to the server via
        # update_settings. No "Plan" entry here on purpose -- Plan vs Code is
        # now decided by the Auto-decide checkbox below (or the hidden
        # agent_type_combo it drives), not picked from this list, so it
        # doesn't show up as a choice in two places.
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("ComposerPill")
        self.mode_combo.addItem("Bypass permissions", userData="bypass")
        self.mode_combo.addItem("Auto", userData="auto")
        self.mode_combo.addItem("Manual", userData="manual")
        self.mode_combo.setToolTip(
            "Bypass permissions: no confirmation, no safety analyzer (fastest, least safe).\n"
            "Auto: safety analyzer checks each action, pauses only for risky ones.\n"
            "Manual: ask for confirmation before every action."
        )
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        composer_tools_row.addWidget(self.mode_combo)

        # Auto-decide Plan vs Code (see classify_plan_or_code): borrowed from
        # how Claude Code itself judges whether a request needs a plan before
        # touching anything. Toggled from Settings -> LLM only. The
        # Code/Plan combo above stays exactly as it always was (always
        # enabled, picked manually) -- when auto-decide is on, its value is
        # simply not read in _send (see agent_type = None there); it never
        # gets disabled or overridden in place.
        self._auto_decide_plan_code = self._settings.value("auto_decide_plan_code", True, type=bool)

        # Presets/MemPalace/code-block used to each be their own always-
        # visible square button; collapsed into one overflow menu so the
        # bar shows the minimum -- agent type, attach (up in the input
        # line), more, send/stop -- with everything else still one click
        # away instead of gone.
        self.presets_btn = QPushButton()
        self.presets_btn.clicked.connect(self._show_presets_menu)
        self.mempalace_btn = QPushButton()
        self.mempalace_btn.setEnabled(False)
        self.mempalace_btn.clicked.connect(self._save_to_mempalace)
        self.code_block_btn = QPushButton()
        self.code_block_btn.clicked.connect(self._insert_code_block)

        self.more_btn = QPushButton("⋯")
        self.more_btn.setObjectName("ComposerSquareButton")
        self.more_btn.setToolTip("More: presets, MemPalace, code block")
        self.more_btn.clicked.connect(self._show_composer_more_menu)
        composer_tools_row.addWidget(self.more_btn)
        composer_tools_row.addStretch()

        self.send_btn = QPushButton()
        self.send_btn.setObjectName("SendButton")
        self.send_btn.setIcon(icon("send", 15, "#ffffff"))
        self.send_btn.setFixedSize(32, 28)
        self.send_btn.setToolTip("Send (Ctrl+Enter)")
        self.send_btn.clicked.connect(self._send)
        composer_tools_row.addWidget(self.send_btn)

        self.stop_btn = _HoldStopButton()
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.setIcon(icon("stop", 15))
        self.stop_btn.setFixedSize(32, 28)
        self.stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_btn.setToolTip("Click to pause. Hold for 5 seconds to stop immediately.")
        self.stop_btn.pause_requested.connect(self._pause_agent)
        self.stop_btn.stop_requested.connect(self._interrupt)
        composer_tools_row.addWidget(self.stop_btn)

        composer_layout.addLayout(composer_tools_row)
        input_row_layout.addWidget(composer_frame)

        self.content_splitter.addWidget(input_row)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self._restore_content_splitter_state()

        # --- bottom status bar ---
        status_bar = QWidget()
        status_bar.setObjectName("StatusBarWidget")
        status_bar.setFixedHeight(STATUS_BAR_HEIGHT)
        status_bar_layout = QHBoxLayout(status_bar)
        status_bar_layout.setContentsMargins(SPACE_MD, 4, SPACE_MD, 4)
        status_bar_layout.setSpacing(SPACE_SM)
        self.state_label = QLabel("No conversation")
        status_bar_layout.addWidget(self.state_label)
        self.duration_label = QLabel("")
        self.duration_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self.duration_label.setToolTip("How long this conversation has been running")
        status_bar_layout.addWidget(self.duration_label)
        self.continue_as_code_btn = QPushButton("Continue as Code →")
        self.continue_as_code_btn.setObjectName("ContinueAsCodeButton")
        self.continue_as_code_btn.setFixedHeight(STATUS_BAR_HEIGHT - 6)
        self.continue_as_code_btn.setToolTip(
            "Start a new Code (read/write) conversation linked to this finished Plan, "
            "via parent_conversation_id"
        )
        self.continue_as_code_btn.setVisible(False)
        self.continue_as_code_btn.clicked.connect(self._continue_as_code)
        status_bar_layout.addWidget(self.continue_as_code_btn)

        self.auto_continue_as_code_check = QCheckBox("Auto")
        self.auto_continue_as_code_check.setToolTip(
            "Automatically continue as Code as soon as a Plan conversation finishes, "
            "without waiting for the button to be clicked."
        )
        self.auto_continue_as_code_check.setChecked(
            self._settings.value("auto_continue_as_code", False, type=bool)
        )
        self.auto_continue_as_code_check.toggled.connect(self._on_auto_continue_as_code_toggled)
        status_bar_layout.addWidget(self.auto_continue_as_code_check)

        self.stuck_nudge_btn = QPushButton("Interrupt && nudge →")
        self.stuck_nudge_btn.setObjectName("StuckNudgeButton")
        self.stuck_nudge_btn.setFixedHeight(STATUS_BAR_HEIGHT - 6)
        self.stuck_nudge_btn.setToolTip(
            "Stop the agent and pre-fill a message telling it to stop repeating itself "
            "and read a specific file directly instead"
        )
        self.stuck_nudge_btn.setVisible(False)
        self.stuck_nudge_btn.clicked.connect(self._interrupt_and_nudge)
        status_bar_layout.addWidget(self.stuck_nudge_btn)

        status_bar_layout.addWidget(self._vsep())
        self.tool_call_label = QLabel("")
        status_bar_layout.addWidget(self.tool_call_label)

        self.compact_btn = QPushButton("Compact now")
        self.compact_btn.setObjectName("CompactButton")
        self.compact_btn.setFixedHeight(STATUS_BAR_HEIGHT - 6)
        self.compact_btn.setToolTip(
            "Force the conversation history to be condensed right now, like Claude "
            "Code's /compact -- POST {conversation_url}/condense"
        )
        self.compact_btn.setEnabled(False)
        self.compact_btn.clicked.connect(self._compact_now)
        status_bar_layout.addWidget(self.compact_btn)
        status_bar_layout.addWidget(self._vsep())
        self.workspace_status_label = QLabel("Workspace: (server-configured)")
        status_bar_layout.addWidget(self.workspace_status_label)
        status_bar_layout.addWidget(self._vsep())
        self.model_status_label = QLabel("")
        status_bar_layout.addWidget(self.model_status_label)
        status_bar_layout.addStretch()
        status_bar_layout.addWidget(self._vsep())
        self.connection_status_label = QPushButton("Disconnected")
        self.connection_status_label.setFlat(True)
        self.connection_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connection_status_label.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0px; text-align: left; }"
        )
        self.connection_status_label.setToolTip("Click to try starting LM Studio's server if disconnected")
        self.connection_status_label.clicked.connect(self._on_connection_status_clicked)
        status_bar_layout.addWidget(self.connection_status_label)
        main_layout.addWidget(status_bar)

        self._new_controller()
        self._check_health()
        asyncio.ensure_future(self._load_profiles_async())
        asyncio.ensure_future(self._init_history_and_refresh())

        # Detects model swaps LM Studio does on its own (e.g. JIT-loading a
        # different model on demand -- confirmed live 2026-07-28), so the
        # Model chip doesn't silently claim a profile is active when a
        # different model is actually loaded.
        self._model_sync_timer = QTimer(self)
        self._model_sync_timer.setInterval(15000)
        self._model_sync_timer.timeout.connect(self._check_loaded_model)
        self._model_sync_timer.timeout.connect(self._check_health)
        self._model_sync_timer.timeout.connect(self._check_conversation_silence)
        self._model_sync_timer.start()

        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_duration_label)
        self._duration_timer.start()

    @staticmethod
    def _simple_chip(children: list[QWidget]) -> QWidget:
        """A single-line #TopBarChip: icon(s) + label(s) side by side."""
        chip = QWidget()
        chip.setObjectName("TopBarChip")
        chip.setFixedHeight(TOOLBAR_CONTROL_HEIGHT)
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(SPACE_SM, 4, SPACE_SM, 4)
        chip_layout.setSpacing(SPACE_XS)
        for child in children:
            chip_layout.addWidget(child)
        return chip

    @staticmethod
    def _fill_two_line_chip(
        chip: QWidget, icon_name: str, caption: str, value_label: QLabel
    ) -> None:
        """Icon + (caption above value) inside an existing chip container --
        matches the Workspace/Model toolbar chips in the design spec."""
        row = QHBoxLayout(chip)
        row.setContentsMargins(SPACE_SM, 4, SPACE_SM, 4)
        row.setSpacing(SPACE_XS)
        icon_label = QLabel()
        icon_label.setPixmap(icon(icon_name, 18).pixmap(18, 18))
        row.addWidget(icon_label)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        caption_label = QLabel(caption)
        caption_label.setObjectName("ChipCaption")
        text_col.addWidget(caption_label)
        value_label.setObjectName("ChipValue")
        text_col.addWidget(value_label)
        row.addLayout(text_col, 1)

    @staticmethod
    def _vsep() -> QFrame:
        line = QFrame()
        line.setObjectName("StatusSeparator")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedHeight(14)
        return line

    def _on_composer_focus_change(self, focused: bool) -> None:
        self.composer_frame.setProperty("focused", "true" if focused else "false")
        _repolish(self.composer_frame)

    # --- controller lifecycle -------------------------------------------------

    def _new_controller(self) -> None:
        if self._watchdog is not None:
            # old_controller.disconnect(self) below only drops signals bound
            # to slots owned by *this* window -- the watchdog's own bound
            # methods aren't "self", so they'd survive that and keep firing
            # against a stopped controller unless detached explicitly here.
            self._watchdog.detach()
            self._watchdog = None
        if self._controller is not None:
            old_controller = self._controller
            try:
                old_controller.disconnect(self)
            except (RuntimeError, TypeError):
                pass
            asyncio.ensure_future(old_controller.stop())
        controller = ConversationController(self._client)
        controller.events_received.connect(self._on_events)
        controller.state_changed.connect(self._on_state_changed)
        controller.error_occurred.connect(self._on_error)
        controller.conversation_ready.connect(self._on_conversation_ready)
        controller.starting_changed.connect(self._on_controller_starting_changed)
        controller.compact_finished.connect(self._on_compact_finished)
        controller.token_usage_changed.connect(self._on_token_usage_changed)
        self._controller = controller
        self._auto_continue_triggered = False
        self._plan_waiting_conversation_id = None
        self._last_event_at = None
        if self._auto_supervise:
            self._watchdog = ConversationWatchdog(controller, on_step=self._on_watchdog_step)

    def _on_controller_starting_changed(self, starting: bool) -> None:
        self._conversation_starting = starting
        self._update_send_enabled()

    def _update_send_enabled(self) -> None:
        self.send_btn.setEnabled(not self._conversation_starting and not self._model_switching)

    def _set_model_switching(self, switching: bool) -> None:
        self._model_switching = switching
        self.model_combo.setEnabled(not switching)
        has_conversation = self._controller is not None and self._controller.conversation_id is not None
        self.agent_type_combo.setEnabled(not switching and not has_conversation)
        self._update_send_enabled()

    def _start_fresh_conversation(self) -> None:
        self._conversation_starting = False
        self._model_switching = False
        self._conversation_started_at = None
        self._duration_frozen = False
        self.duration_label.setText("")
        self._new_controller()
        self.log.clear()
        self.stack.setCurrentWidget(self.welcome)
        self.state_label.setText("No conversation")
        self.state_label.setObjectName("")
        _repolish(self.state_label)
        self.agent_type_combo.setEnabled(True)
        self.changes_btn.setEnabled(False)
        self.mempalace_btn.setEnabled(False)
        self.browser_preview_btn.setEnabled(False)
        self._error_history = []
        self._update_errors_button()
        self._current_agent_type = None
        self._recent_user_messages = []
        self.continue_as_code_btn.setVisible(False)
        self.stuck_nudge_btn.setVisible(False)
        self._tool_call_count = 0
        self._estimated_context_chars = 0
        self._real_used_tokens = 0
        self._real_context_window = 0
        self._auto_compact_triggered = False
        self._update_tool_call_label()
        self.input.setPlainText("")
        self.input.setFocus()
        self._update_send_enabled()

    def _open_changes(self) -> None:
        if self._controller is None or self._controller.conversation_id is None:
            return
        dialog = ChangesDialog(self, self._client, self._controller.conversation_id)
        dialog.exec()

    def _open_browser_preview(self) -> None:
        if self._controller is None or self._controller.conversation_id is None:
            return
        asyncio.ensure_future(self._open_browser_preview_async(self._controller.conversation_id))

    async def _open_browser_preview_async(self, conversation_id: str) -> None:
        try:
            conversation = await self._client.get_conversation(conversation_id)
            if not conversation.sandbox_id:
                self._on_error("No sandbox is attached to this conversation yet.")
                return
            url = await self._client.get_novnc_url(conversation.sandbox_id)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not open browser preview: {exc}")
            return
        if not url:
            self._on_error("No browser preview available for this sandbox.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _open_supervised_agent(self) -> None:
        dialog = SupervisedAgentDialog(self._client, self)
        dialog.exec()

    def _update_errors_button(self) -> None:
        count = len(self._error_history)
        self.errors_btn.setText(f"Errors ({count})" if count else "Errors")
        self.errors_btn.setEnabled(count > 0)

    def _open_errors_dialog(self) -> None:
        if not self._error_history:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Errors ({len(self._error_history)})")
        dialog.resize(700, 500)
        layout = QVBoxLayout(dialog)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setStyleSheet("font-family: monospace; font-size: 12px;")
        parts = [
            f"[{ts.strftime('%H:%M:%S')}] {text}" for ts, text in self._error_history
        ]
        view.setPlainText("\n\n".join(parts))
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _open_task_board(self) -> None:
        dialog = TaskBoardDialog(self, self._client)
        dialog.open_requested.connect(self._resume_conversation)
        dialog.conversations_deleted.connect(self._on_conversations_deleted_elsewhere)
        dialog.exec()

    def _on_conversations_deleted_elsewhere(self, deleted_ids: list[str]) -> None:
        # Mission Control has no other way to tell this window that the
        # conversation it's currently showing (log, Errors count, status
        # pill) was just deleted from there instead of from the sidebar --
        # without this, deleting it via Mission Control left this window
        # showing a stale log/error count for a conversation that no
        # longer exists server-side at all.
        current_id = self._controller.conversation_id if self._controller is not None else None
        if current_id is not None and current_id in deleted_ids:
            self._start_fresh_conversation()
        asyncio.ensure_future(self._drop_deleted_history_and_refresh(deleted_ids))

    async def _drop_deleted_history_and_refresh(self, deleted_ids: list[str]) -> None:
        for deleted_id in deleted_ids:
            await self._history.delete(deleted_id)
        await self._refresh_sidebar_history_async()

    def _save_to_mempalace(self) -> None:
        if self._controller is None or self._controller.conversation_id is None:
            return
        instruction = (
            "Please call mempalace_checkpoint now to save a diary entry (and any relevant "
            "drawer items) summarizing this session, in AAAK format."
        )
        # kind="system", not "user": sent by the app (a toolbar button), not
        # typed by the person -- same reasoning as the other auto-generated
        # nudge/correction messages below.
        self._append_log(instruction, kind="system")
        self._controller.send_message(instruction)

    def _insert_attachment_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Attach file")
        if not path:
            return
        self._insert_text_at_cursor(f"Please inspect this file: {path}\n")

    def _insert_code_block(self) -> None:
        cursor = self.input.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        if selected:
            cursor.insertText(f"```\n{selected}\n```")
        else:
            cursor.insertText("```\n\n```")
            cursor.movePosition(
                QTextCursor.MoveOperation.PreviousCharacter,
                QTextCursor.MoveMode.MoveAnchor,
                4,
            )
            self.input.setTextCursor(cursor)
        self.input.setFocus()

    def _insert_text_at_cursor(self, text: str) -> None:
        cursor = self.input.textCursor()
        current = self.input.toPlainText()
        prefix = "\n" if current and not current.endswith("\n") and not text.startswith("\n") else ""
        cursor.insertText(prefix + text)
        self.input.setTextCursor(cursor)
        self.input.setFocus()

    def _confirm_delete_conversation(self, conversation_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete conversation",
            "Delete this conversation? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            asyncio.ensure_future(self._delete_conversation_async(conversation_id))

    async def _delete_conversation_async(self, conversation_id: str) -> None:
        # A Continue-as-Code parent/child pair shares one sandbox container
        # server-side, and the server only actually tears the sandbox down
        # once EVERY conversation referencing it is gone (see
        # app_conversation_router.py's _finalize_sandbox_delete: "delete the
        # sandbox if unreferenced") -- confirmed live 2026-07-31: deleting
        # only the visible half left the container running because the
        # other half (parent or child) still referenced it. Delete the
        # whole family, not just the one id the user clicked -- resolved via
        # resolve_conversation_family, NOT sub_conversation_ids (confirmed
        # unreliable/empty even on a parent with a real running child).
        family_ids = {conversation_id}
        try:
            all_conversations = await self._client.search_conversations()
            family_ids = resolve_conversation_family(conversation_id, all_conversations)
        except Exception:  # noqa: BLE001 -- best effort; still delete the one id we know about
            pass
        for family_id in family_ids:
            try:
                await self._client.delete_conversation(family_id)
            except Exception:  # noqa: BLE001 -- server-side record may already be gone; still drop it locally
                pass
            await self._history.delete(family_id)
            if self._controller is not None and self._controller.conversation_id == family_id:
                self._start_fresh_conversation()
        await self._refresh_sidebar_history_async()

    def _open_settings(self) -> None:
        # Every real section needs the current settings blob from the server
        # (agent/condenser/verification/mcp/application all live in one GET
        # /api/v1/settings response) before the dialog can show real values,
        # so this kicks off an async fetch rather than constructing the
        # dialog synchronously.
        asyncio.ensure_future(self._open_settings_async())

    async def _open_settings_async(self) -> None:
        try:
            settings = await self._client.get_settings()
            profiles, _active_profile = await self._client.list_llm_profiles()
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Failed to load settings: {exc}")
            return
        # Rebuilt each time (not cached) so it always reflects the latest
        # server state rather than a snapshot from whenever the dialog
        # happened to be first opened -- profiles are fetched fresh here
        # too, not read from self._profiles_by_name, which is only
        # refreshed on app startup / explicit model-chip reloads and would
        # otherwise miss a profile created after that.
        self._settings_dialog = SettingsDialog(
            self,
            client=self._client,
            settings=settings,
            profiles=profiles,
            active_profile_name=self.model_combo.currentData() or active_profile,
            default_plan_model=self._settings.value("default_plan_model_name") or None,
            default_code_model=self._settings.value("default_code_model_name") or None,
            auto_decide_plan_code=self._auto_decide_plan_code,
            lm_studio_context_length=self._settings.value(
                "lm_studio_context_length", DEFAULT_LM_STUDIO_CONTEXT_LENGTH, type=int
            ),
            auto_supervise=self._auto_supervise,
        )
        self._settings_dialog.profile_activate_requested.connect(self._activate_llm_profile)
        self._settings_dialog.profiles_changed.connect(
            lambda: asyncio.ensure_future(self._load_profiles_async())
        )
        self._settings_dialog.default_plan_model_changed.connect(
            lambda name: self._settings.setValue("default_plan_model_name", name)
        )
        self._settings_dialog.default_code_model_changed.connect(
            lambda name: self._settings.setValue("default_code_model_name", name)
        )
        self._settings_dialog.auto_decide_plan_code_changed.connect(self._on_auto_plan_toggled)
        self._settings_dialog.lm_studio_context_length_changed.connect(self._on_context_length_changed)
        self._settings_dialog.auto_supervise_changed.connect(self._on_auto_supervise_toggled)
        # Custom instructions are read once at startup and delivered per
        # conversation, so re-read them when the dialog closes -- otherwise
        # an edit would not take effect until the app restarts.
        self._settings_dialog.finished.connect(
            lambda _result: asyncio.ensure_future(self._load_custom_instructions())
        )
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _activate_llm_profile(self, name: str) -> None:
        # Server-side activation happened inside the dialog. This also syncs
        # the toolbar's Model chip; a newly-created profile may only appear in
        # the combo after the immediately following profiles reload.
        self._settings.setValue("last_profile_name", name)
        self._switch_to_named_model(name)

    def _ensure_default_model_settings(self) -> None:
        defaults = {
            "default_plan_model_name": "lmstudio-qwen36-35b-q4",
            "default_code_model_name": "lmstudio-qwen36-27b-q4",
        }
        changed = False
        for key, value in defaults.items():
            if not self._settings.value(key):
                self._settings.setValue(key, value)
                changed = True
        if changed:
            self._settings.sync()

    def _switch_to_named_model(self, profile_name: str) -> None:
        index = self.model_combo.findData(profile_name)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)

    def _on_agent_type_changed(self, _index: int) -> None:
        # Client-only convenience (Settings -> LLM -> Default Plan/Code
        # model): only applies while still picking the agent type for a
        # conversation that hasn't started yet -- the combo is disabled once
        # a conversation is live, so this can't fire mid-conversation.
        if not self.agent_type_combo.isEnabled():
            return
        agent_type = self.agent_type_combo.currentData()
        key = "default_plan_model_name" if agent_type == "plan" else "default_code_model_name"
        default_model = self._settings.value(key)
        if default_model:
            previous = self.model_combo.currentData()
            self._switch_to_named_model(default_model)
            if previous == default_model:
                asyncio.ensure_future(self._activate_profile_async(default_model))

    _MODE_SETTINGS = {
        "bypass": (False, "none"),
        "auto": (False, "llm"),
        "manual": (True, "llm"),
    }

    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode_combo.currentData()
        confirmation_mode, security_analyzer = self._MODE_SETTINGS[mode]
        asyncio.ensure_future(
            self._push_mode_settings(confirmation_mode, security_analyzer)
        )

    def _on_auto_plan_toggled(self, checked: bool) -> None:
        self._auto_decide_plan_code = checked
        self._settings.setValue("auto_decide_plan_code", checked)

    def _on_context_length_changed(self, value: int) -> None:
        self._settings.setValue("lm_studio_context_length", value)
        asyncio.ensure_future(self._sync_context_derived_settings(value))

    def _on_auto_supervise_toggled(self, checked: bool) -> None:
        self._auto_supervise = checked
        self._settings.setValue("auto_supervise", checked)
        # Applies immediately to whatever conversation is already running,
        # not just the next one -- flipping this mid-run shouldn't need a
        # restart to take effect.
        if checked and self._watchdog is None and self._controller is not None:
            self._watchdog = ConversationWatchdog(self._controller, on_step=self._on_watchdog_step)
        elif not checked and self._watchdog is not None:
            self._watchdog.detach()
            self._watchdog = None

    async def _sync_context_derived_settings(self, context_length: int) -> None:
        """Keeps max_input_tokens (per local profile) and condenser.max_tokens
        (global) proportional to the LM Studio context length setting, using
        the same ratios verified live 2026-07-31 at context_length=131072
        (max_input_tokens=110000, condenser.max_tokens=90000) -- enough
        headroom below the actual loaded context for output tokens and
        per-request overhead without wasting most of a larger context on
        margin. Only touches profiles pointing at this app's own LM Studio
        bridge address; a cloud/remote profile's limits have nothing to do
        with this machine's GPU."""
        max_input_tokens = round(context_length * 110000 / 131072)
        condenser_max_tokens = round(context_length * 90000 / 131072)
        local = profile_base_url("http://127.0.0.1:1234/v1")
        try:
            await self._client.update_settings(
                {"agent_settings_diff": {"condenser": {"max_tokens": condenser_max_tokens}}}
            )
            profiles, _ = await self._client.list_llm_profiles()
            updated = 0
            for profile in profiles:
                if not same_server(profile.base_url, local):
                    continue
                detail = await self._client.get_profile_detail(profile.name)
                config = detail.get("config") or {}
                await self._client.save_profile(
                    profile.name,
                    model=profile.model,
                    base_url=profile.base_url,
                    preserve_existing_api_key=True,
                    max_input_tokens=max_input_tokens,
                    extra_config=config,
                )
                updated += 1
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not sync context-derived settings: {exc}")
            return
        self._append_log(
            f"Synced to context length {context_length}: max_input_tokens={max_input_tokens} "
            f"on {updated} local profile(s), condenser.max_tokens={condenser_max_tokens}.",
            kind="system",
        )

    async def _push_mode_settings(self, confirmation_mode: bool, security_analyzer: str) -> None:
        payload = {
            "conversation_settings_diff": {
                "confirmation_mode": confirmation_mode,
                "security_analyzer": security_analyzer,
            }
        }
        try:
            await self._client.update_settings(payload)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not update verification settings: {exc}")

    async def _activate_profile_async(self, profile_name: str) -> None:
        if self._model_switching:
            return
        self._set_model_switching(True)
        try:
            await self._ensure_profile_ready(profile_name)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Failed to activate LLM profile {profile_name}: {exc}")
        finally:
            self._set_model_switching(False)

    async def _ensure_profile_ready(self, profile_name: str) -> None:
        profile = self._profiles_by_name.get(profile_name)
        if profile is None:
            raise LookupError(f"LLM profile {profile_name!r} was not found")
        context_length = self._settings.value(
            "lm_studio_context_length", DEFAULT_LM_STUDIO_CONTEXT_LENGTH, type=int
        )
        await ensure_profile_model_loaded(profile_name, profile.model, context_length=context_length)
        await self._client.activate_profile(profile_name)
        self._check_loaded_model()

    def _resume_conversation(self, conversation_id: str) -> None:
        # Reattaching, not creating: _on_conversation_ready must not
        # overwrite this conversation's existing history record (title,
        # model) with whatever a previous *new*-conversation attempt left
        # in _pending_title/_pending_model, or possibly None.
        self._is_resuming = True
        self._new_controller()
        self.log.clear()
        self._error_history = []
        self._update_errors_button()
        self.stack.setCurrentWidget(self.log)
        self._append_log(f"[reattaching to conversation {conversation_id}…]")
        self.agent_type_combo.setEnabled(False)
        self.stuck_nudge_btn.setVisible(False)
        self._tool_call_count = 0  # _render_event recounts from the full backfilled history
        self._estimated_context_chars = 0
        self._real_used_tokens = 0
        self._real_context_window = 0
        self._auto_compact_triggered = False
        self._recent_user_messages = []
        self._update_tool_call_label()
        self._controller.attach(conversation_id)

    def _on_suggestion_clicked(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.setFocus()

    # --- presets ---------------------------------------------------------------

    def _show_presets_menu(self) -> None:
        menu = QMenu(self)
        save_action = menu.addAction("Save current as preset…")
        save_action.triggered.connect(self._save_current_as_preset)
        if self._preset_list:
            menu.addSeparator()
            for preset in self._preset_list:
                action = menu.addAction(preset.name)
                action.triggered.connect(lambda _checked=False, p=preset: self._apply_preset(p))
        menu.addSeparator()
        manage_action = menu.addAction("Manage presets…")
        manage_action.triggered.connect(self._open_manage_presets)
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def _show_composer_more_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("ConversationActionsMenu")
        presets_action = menu.addAction(icon("history", 16), "Saved presets")
        presets_action.triggered.connect(self._show_presets_menu)
        mempalace_action = menu.addAction(icon("git-history", 16), "Save to MemPalace")
        mempalace_action.setEnabled(self.mempalace_btn.isEnabled())
        mempalace_action.triggered.connect(self._save_to_mempalace)
        code_action = menu.addAction(icon("code", 16), "Insert code block")
        code_action.triggered.connect(self._insert_code_block)
        menu.addSeparator()
        new_window_action = menu.addAction(icon("model-ai", 16), "New window")
        new_window_action.setToolTip(
            "Open a second, fully independent window -- its own conversation, "
            "responsive even while this one is busy working."
        )
        new_window_action.triggered.connect(self._open_new_window)
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def _show_topbar_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("ConversationActionsMenu")

        unload_action = menu.addAction(icon("model-ai", 16), "Unload model")
        unload_action.setEnabled(self.unload_model_btn.isEnabled())
        unload_action.triggered.connect(self._on_unload_model_clicked)

        menu.addSeparator()
        think_action = menu.addAction("Thinking")
        think_action.setCheckable(True)
        think_action.setChecked(self.enable_thinking_check.isChecked())
        think_action.setEnabled(True)
        think_action.toggled.connect(self.enable_thinking_check.setChecked)

        keep_action = menu.addAction("Keep thinking")
        keep_action.setCheckable(True)
        keep_action.setChecked(self.preserve_thinking_check.isChecked())
        keep_action.setEnabled(True)
        keep_action.toggled.connect(self.preserve_thinking_check.setChecked)

        split_menu = menu.addMenu(icon("panel", 16), "Split view")
        self._populate_split_view_menu(split_menu)

        menu.addSeparator()
        history_action = menu.addAction(icon("history", 16), "History")
        history_action.triggered.connect(self._refresh_sidebar_history)

        changes_action = menu.addAction(icon("git-history", 16), "Changes")
        changes_action.setEnabled(self.changes_btn.isEnabled())
        changes_action.triggered.connect(self._open_changes)

        browser_action = menu.addAction(icon("browser", 16), "Browser")
        browser_action.setEnabled(self.browser_preview_btn.isEnabled())
        browser_action.triggered.connect(self._open_browser_preview)

        errors_text = self.errors_btn.text() or "Errors"
        errors_action = menu.addAction(icon("error", 16), errors_text)
        errors_action.setEnabled(self.errors_btn.isEnabled())
        errors_action.triggered.connect(self._open_errors_dialog)

        tasks_action = menu.addAction(icon("plan-tasks", 16), "Tasks")
        tasks_action.triggered.connect(self._open_task_board)

        supervised_action = menu.addAction(icon("run-task", 16), "Supervised Agent")
        supervised_action.triggered.connect(self._open_supervised_agent)

        menu.exec(self.topbar_menu_btn.mapToGlobal(self.topbar_menu_btn.rect().bottomLeft()))

    def _populate_split_view_menu(self, menu: QMenu) -> None:
        menu.setObjectName("SplitViewMenu")
        menu.setStyleSheet(
            f"QMenu#SplitViewMenu {{ background-color: {BG_SURFACE_1}; border: 1px solid {BORDER}; "
            "border-radius: 5px; padding: 3px; }}"
            "QMenu#SplitViewMenu::item { padding: 0px; margin: 0px; }"
            f"QPushButton#SplitModeOption {{ background-color: {BG_SURFACE_2}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 4px; padding: 2px 5px; "
            "text-align: left; font-size: 11px; font-weight: 600; }}"
            f"QPushButton#SplitModeOption:hover {{ background-color: {BG_SURFACE_3}; "
            f"border-color: {BORDER_HOVER}; }}"
            f"QPushButton#SplitModeOption:checked {{ background-color: {BG_SURFACE_3}; "
            f"border: 1px solid {COLOR_PRIMARY}; color: {TEXT_PRIMARY}; }}"
        )
        labels = {
            "combined": "Combined",
            "left": "Activity on left",
            "right": "Activity on right",
            "top": "Activity on top",
            "bottom": "Activity on bottom",
        }
        for mode, label in labels.items():
            action = QWidgetAction(menu)
            action.setData(mode)
            option = QPushButton(label)
            option.setObjectName("SplitModeOption")
            option.setIcon(_split_layout_icon(mode))
            option.setIconSize(QSize(38, 24))
            option.setCheckable(True)
            option.setChecked(mode == self._split_view_mode)
            option.setMinimumSize(174, 34)
            option.clicked.connect(
                lambda _checked=False, selected=mode, popup=menu: self._choose_split_view_mode(
                    selected, popup
                )
            )
            action.setDefaultWidget(option)
            menu.addAction(action)

    def _choose_split_view_mode(self, mode: str, menu: QMenu) -> None:
        self._set_split_view_mode(mode)
        menu.close()

    def _set_split_view_mode(self, mode: str) -> None:
        if mode not in ("combined", "left", "right", "top", "bottom"):
            return
        self._split_view_mode = mode
        enabled = mode != "combined"
        self.split_view_btn.setChecked(enabled)
        self._settings.setValue("split_activity_view", enabled)
        if enabled:
            self._settings.setValue("split_activity_position", mode)
        if hasattr(self, "log"):
            if enabled:
                self.log.set_split_position(mode)
            self.log.set_split_enabled(enabled)
        for action in self.split_view_menu.actions():
            option = action.defaultWidget() if isinstance(action, QWidgetAction) else None
            if isinstance(option, QPushButton):
                option.setChecked(action.data() == mode)

    def _open_new_window(self) -> None:
        # Shares this process's AppServerClient and the two MCP servers
        # (AskUserServer/WorkspaceFolderServer) -- they're bound to fixed
        # ports (see the single-instance guard in main.py), so a second
        # window creating its OWN instances would just fail to bind, not
        # get a working second copy. Only the last window still open when
        # one closes actually tears those down -- see _shutdown_before_close.
        window = MainWindow(
            self._client,
            history_store=self._history,
            shared_ask_user_server=self._ask_user_server,
            shared_workspace_server=self._workspace_server,
        )
        window.setWindowIcon(self.windowIcon())
        window.show()

    def _save_current_as_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = name.strip()
        if not ok or not name:
            return
        preset = Preset(
            name=name,
            agent_type=self.agent_type_combo.currentData() or "default",
            llm_profile_name=self.model_combo.currentData(),
            starter_instructions=self.input.toPlainText().strip() or None,
        )
        asyncio.ensure_future(self._save_preset_async(preset))

    async def _save_preset_async(self, preset: Preset) -> None:
        await self._presets.save(preset)
        await self._reload_presets_async()

    def _apply_preset(self, preset: Preset) -> None:
        index = self.agent_type_combo.findData(preset.agent_type)
        if index >= 0:
            self.agent_type_combo.setCurrentIndex(index)
        if preset.llm_profile_name:
            model_index = self.model_combo.findData(preset.llm_profile_name)
            if model_index >= 0:
                self.model_combo.setCurrentIndex(model_index)
        if preset.starter_instructions:
            self.input.setPlainText(preset.starter_instructions)
        self.input.setFocus()

    def _open_manage_presets(self) -> None:
        dialog = _PresetManagerDialog(self, self._preset_list)
        dialog.delete_requested.connect(self._delete_preset)
        dialog.exec()

    def _delete_preset(self, name: str) -> None:
        asyncio.ensure_future(self._delete_preset_async(name))

    async def _delete_preset_async(self, name: str) -> None:
        await self._presets.delete(name)
        await self._reload_presets_async()

    # --- profiles / model selection -------------------------------------------

    async def _load_profiles_async(self) -> None:
        try:
            profiles, active_profile = await self._client.list_llm_profiles()
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Failed to load LLM profiles: {exc}")
            return
        self._profiles_by_name = {p.name: p for p in profiles}
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("No model", userData=None)
        for p in profiles:
            label = f"{p.name} ({p.model})" if p.model else p.name
            self.model_combo.addItem(icon("model-ai"), label, userData=p.name)

        # Start deliberately without selecting or loading a model.  Profiles
        # stay available in the menu and are activated only after the user
        # chooses one.
        self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self._update_model_status_label()
        self._refresh_thinking_toggles()
        self._check_loaded_model(auto_select=False)

    def _on_model_selection_changed(self, _index: int) -> None:
        name = self.model_combo.currentData()
        if name:
            self._settings.setValue("last_profile_name", name)
            asyncio.ensure_future(self._activate_profile_async(name))
        self._update_model_status_label()
        self._refresh_thinking_toggles()

    def _refresh_thinking_toggles(self) -> None:
        name = self.model_combo.currentData()
        if not name:
            self.enable_thinking_check.setEnabled(True)
            self.preserve_thinking_check.setEnabled(True)
            self.enable_thinking_check.setToolTip("Thinking preference is staged until a model profile is selected.")
            self.preserve_thinking_check.setToolTip("Keep-thinking preference is staged until a model profile is selected.")
            return
        self.enable_thinking_check.setEnabled(True)
        self.preserve_thinking_check.setEnabled(True)
        self.enable_thinking_check.setToolTip(
            "Thinking: enable model reasoning for the selected LLM profile. "
            "Applies to the next new conversation."
        )
        self.preserve_thinking_check.setToolTip(
            "Keep thinking: preserve previous thinking blocks across turns for the selected LLM profile. "
            "Applies to the next new conversation."
        )
        asyncio.ensure_future(self._refresh_thinking_toggles_async(name))

    async def _refresh_thinking_toggles_async(self, profile_name: str) -> None:
        try:
            detail = await self._client.get_profile_detail(profile_name)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Failed to load thinking settings for {profile_name}: {exc}")
            return
        finally:
            # Without this, a failed load left the toggles permanently
            # disabled/greyed-out with no way to retry -- the user just sees
            # an unresponsive switch and (reasonably) assumes toggling it did
            # nothing, even though nothing was ever actually loaded to toggle.
            if profile_name == self.model_combo.currentData():
                self.enable_thinking_check.setEnabled(True)
                self.preserve_thinking_check.setEnabled(True)
        if profile_name != self.model_combo.currentData():
            return
        config = detail.get("config") or {}
        extra_body = config.get("litellm_extra_body") or {}
        template_kwargs = extra_body.get("chat_template_kwargs") or {}
        self.enable_thinking_check.blockSignals(True)
        self.preserve_thinking_check.blockSignals(True)
        self.enable_thinking_check.setChecked(bool(template_kwargs.get("enable_thinking", False)))
        self.preserve_thinking_check.setChecked(bool(template_kwargs.get("preserve_thinking", False)))
        self._update_thinking_toggle_icons()
        self.enable_thinking_check.blockSignals(False)
        self.preserve_thinking_check.blockSignals(False)

    def _on_thinking_toggle_changed(self, _checked: bool) -> None:
        self._update_thinking_toggle_icons()
        name = self.model_combo.currentData()
        if not name:
            return
        asyncio.ensure_future(
            self._save_thinking_toggles_async(
                name,
                self.enable_thinking_check.isChecked(),
                self.preserve_thinking_check.isChecked(),
            )
        )

    async def _save_thinking_toggles_async(
        self, profile_name: str, enable_thinking: bool, preserve_thinking: bool
    ) -> None:
        try:
            detail = await self._client.get_profile_detail(profile_name)
            config = dict(detail.get("config") or {})
            extra_body = dict(config.get("litellm_extra_body") or {})
            template_kwargs = dict(extra_body.get("chat_template_kwargs") or {})
            template_kwargs["enable_thinking"] = enable_thinking
            template_kwargs["preserve_thinking"] = preserve_thinking
            extra_body["chat_template_kwargs"] = template_kwargs
            await self._client.save_profile(
                profile_name,
                model=config["model"],
                base_url=config.get("base_url"),
                preserve_existing_api_key=True,
                temperature=config.get("temperature"),
                top_p=config.get("top_p"),
                top_k=config.get("top_k"),
                reasoning_effort=config.get("reasoning_effort"),
                max_input_tokens=config.get("max_input_tokens"),
                max_output_tokens=config.get("max_output_tokens"),
                stream=config.get("stream"),
                caching_prompt=config.get("caching_prompt"),
                native_tool_calling=config.get("native_tool_calling"),
                enable_encrypted_reasoning=config.get("enable_encrypted_reasoning"),
                prompt_cache_retention=config.get("prompt_cache_retention"),
                extended_thinking_budget=config.get("extended_thinking_budget"),
                litellm_extra_body=extra_body,
                extra_config=config,
            )
            await self._client.activate_profile(profile_name)
            self._push_llm_toggle_live(config, extra_body)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Failed to save thinking settings for {profile_name}: {exc}")
        finally:
            if profile_name == self.model_combo.currentData():
                self.enable_thinking_check.setEnabled(True)
                self.preserve_thinking_check.setEnabled(True)

    def _push_llm_toggle_live(self, config: dict, extra_body: dict) -> None:
        """Applies a just-saved Thinking/Keep toggle to the conversation
        that's actually running right now, if any -- otherwise it would only
        take effect the next time a conversation is started (see
        conversation_controller.switch_llm's usage_id note for why a plain
        re-POST of the same profile wouldn't do anything by itself)."""
        if self._controller is None or self._controller.conversation_id is None:
            return
        if self._controller.llm_model != config.get("model"):
            return  # a different model is actually running -- nothing to push
        live_config = dict(config)
        live_config["litellm_extra_body"] = extra_body
        live_config["usage_id"] = f"agent-live-{int(time.time() * 1000)}"
        self._controller.switch_llm(live_config)
        self._append_log("Applied the Thinking/Keep setting to the running conversation.", kind="system")

    def _update_thinking_toggle_icons(self) -> None:
        _repolish(self.enable_thinking_check)
        _repolish(self.preserve_thinking_check)

    def _update_model_status_label(self) -> None:
        model = self._selected_model()
        self.model_status_label.setText(f"Model: {model}" if model else "")
        self.model_status_label.setObjectName("")
        self.model_status_label.setToolTip("")
        _repolish(self.model_status_label)

    def _check_loaded_model(self, *, auto_select: bool = False) -> None:
        if self._model_check_in_flight:
            return
        asyncio.ensure_future(self._check_loaded_model_async(auto_select=auto_select))

    async def _check_loaded_model_async(self, *, auto_select: bool = False) -> None:
        if self._model_check_in_flight:
            return
        self._model_check_in_flight = True
        detected = None
        try:
            if not self._profiles_by_name:
                return
            detected = await detect_loaded_model()
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Model check failed: {exc}")
            return
        finally:
            self._model_check_in_flight = False
        await self._handle_detected_model(detected, auto_select=auto_select)

    async def _handle_detected_model(self, detected, *, auto_select: bool = False) -> None:
        if detected is None:
            # Distinguish "LM Studio is up but nothing's loaded" from
            # "unreachable" -- the health chip only checks the OpenHands
            # agent-server (port 3000), not LM Studio (port 1234), so a
            # genuinely empty LM Studio previously produced no warning at
            # all right up until a sent message hard-failed with
            # litellm.BadRequestError ("No models loaded").
            state = await probe_llm_server_state()
            if state == "no_model":
                self.model_status_label.setText("Model: none loaded in LM Studio")
                self.model_status_label.setObjectName("ModelStatusUnknown")
                self.model_status_label.setToolTip(
                    "LM Studio is running but has no model loaded -- sending a message "
                    "will fail. Load a model in LM Studio's Developer page or run "
                    "`lms load`."
                )
                _repolish(self.model_status_label)
            return
        loaded_id = detected.model_id

        def _normalize(model: str) -> str:
            return model.removeprefix("openai/").strip().strip("/").lower()

        def _model_keys(model: str) -> set[str]:
            normalized = _normalize(model)
            keys = {normalized}
            if "/" in normalized:
                keys.add(normalized.rsplit("/", 1)[-1])
            return keys

        matched_name = None
        loaded_keys: set[str] = set()
        for alias in detected.aliases or (loaded_id,):
            loaded_keys.update(_model_keys(alias))
        model_matches = []
        for profile in self._profiles_by_name.values():
            profile_keys = _model_keys(profile.name) | _model_keys(profile.model)
            if profile_keys & loaded_keys:
                model_matches.append(profile)
        if model_matches:
            def _match_score(profile) -> tuple[int, int, int]:
                return (
                    int(same_server(profile.base_url, detected.base_url)),
                    int(bool(_model_keys(profile.model) & loaded_keys)),
                    int(bool(_model_keys(profile.name) & loaded_keys)),
                )

            best_score = max(_match_score(profile) for profile in model_matches)
            best_matches = [
                profile for profile in model_matches if _match_score(profile) == best_score
            ]
            current_name = self.model_combo.currentData()
            matched = next(
                (profile for profile in best_matches if profile.name == current_name),
                best_matches[0],
            )
            matched_name = matched.name

        if matched_name is not None:
            if auto_select and matched_name != self.model_combo.currentData():
                self.model_combo.blockSignals(True)
                self._switch_to_named_model(matched_name)
                self.model_combo.blockSignals(False)
                self._settings.setValue("last_profile_name", matched_name)
                try:
                    await self._client.activate_profile(matched_name)
                except Exception as exc:  # noqa: BLE001
                    self._on_error(f"Detected model, but profile activation failed: {exc}")
                self._refresh_thinking_toggles()
            if matched_name != self.model_combo.currentData():
                self.model_status_label.setText(
                    f"Model: {self._selected_model()} (LM Studio loaded: {matched_name})"
                )
                self.model_status_label.setObjectName("ModelStatusUnknown")
                self.model_status_label.setToolTip(
                    "LM Studio has a different model loaded than the selected Plan/Code profile. "
                    "The selected profile will still be used for the next conversation."
                )
                _repolish(self.model_status_label)
            else:
                self._update_model_status_label()  # clears any stale "unknown" state
        else:
            self.model_status_label.setText(f"Model: unknown (loaded: {loaded_id})")
            self.model_status_label.setObjectName("ModelStatusUnknown")
            self.model_status_label.setToolTip(
                "The model actually loaded in LM Studio doesn't match any configured profile. "
                "Add a profile for it in Settings -> LLM (Autodetect can fill the Model field)."
            )
            _repolish(self.model_status_label)

    def _on_unload_model_clicked(self) -> None:
        asyncio.ensure_future(self._unload_model_async())

    async def _unload_model_async(self) -> None:
        running_conversation_id = (
            self._controller.conversation_id
            if self._controller is not None and self._last_run_state == RunState.RUNNING
            else None
        )
        try:
            conversations = await self._client.search_conversations(limit=50)
            other_conversations_running = any(
                c.execution_status is not None
                and c.execution_status.value == "running"
                and c.id != running_conversation_id
                for c in conversations
            )
        except Exception:  # noqa: BLE001 -- best effort; refuse to unload if we can't confirm it's safe
            other_conversations_running = True
        if running_conversation_id is not None or other_conversations_running:
            QMessageBox.information(
                self,
                "Can't unload model",
                "A conversation is still running and depends on the loaded model. "
                "Stop it first, then try again.",
            )
            return
        self.unload_model_btn.setEnabled(False)
        try:
            process = await asyncio.create_subprocess_exec(
                "/home/vojtech/.lmstudio/bin/lms",
                "unload",
                "--all",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=30.0)
        except (OSError, asyncio.TimeoutError) as exc:
            self._on_error(f"Could not unload the model: {exc}")
        finally:
            self.unload_model_btn.setEnabled(True)
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(0)  # "No model"
        self.model_combo.blockSignals(False)
        self._update_model_status_label()
        self._append_log("Model unloaded to free VRAM.", kind="system")

    def _selected_model(self) -> str | None:
        name = self.model_combo.currentData()
        profile = self._profiles_by_name.get(name) if name else None
        return profile.model if profile else None

    # --- health / workspace ----------------------------------------------------

    def _check_health(self) -> None:
        if self._health_check_in_flight:
            return
        asyncio.ensure_future(self._check_health_async())

    async def _check_health_async(self) -> None:
        if self._health_check_in_flight:
            return
        self._health_check_in_flight = True
        ok = False
        llm_state = "unreachable"
        try:
            ok = await self._client.health()
            llm_state = await probe_llm_server_state() if ok else "unreachable"
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Health check failed: {exc}")
        finally:
            self._health_check_in_flight = False
        self._health_full_text = f"Health: {'OK' if ok else 'unreachable'}"
        self.health_label.setText(self._health_full_text)
        self.health_label.setObjectName("HealthOk" if ok else "HealthBad")
        _repolish(self.health_label)
        self.health_icon_label.setPixmap(
            icon("health" if ok else "error", 14).pixmap(14, 14)
        )

        # "Connected" used to mean only "app_server on port 3000 answered" --
        # confirmed live 2026-07-31 that it stayed green with LM Studio
        # (port 1234, the actual LLM backend) completely down, since nothing
        # here ever checked it. probe_llm_server_state() already exists for
        # exactly this gap (see its own docstring, added after an earlier
        # instance of the same confusion) -- it just wasn't wired in here
        # yet. Skipped when app_server itself is down since LM Studio state
        # is irrelevant at that point.
        self._llm_server_state = llm_state
        fully_connected = ok and self._llm_server_state == "loaded"

        self.overall_status_icon_label.setPixmap(
            icon("shield-success" if fully_connected else "error", 14).pixmap(14, 14)
        )
        if not ok:
            overall_text, sublabel_text = "Server unreachable", "Check the server"
        elif self._llm_server_state == "unreachable":
            overall_text, sublabel_text = "LM Studio unreachable", "Click Disconnected below to start it"
        elif self._llm_server_state == "no_model":
            overall_text, sublabel_text = "No model loaded", "Load a model in LM Studio"
        else:
            overall_text, sublabel_text = "All systems operational", "Connected and ready"
        self.overall_status_label.setText(overall_text)
        self.overall_status_label.setObjectName("HealthOk" if fully_connected else "HealthBad")
        _repolish(self.overall_status_label)
        self.overall_status_sublabel.setText(sublabel_text)
        self.connection_status_label.setText("Connected" if fully_connected else "Disconnected")
        self.connection_status_label.setObjectName("HealthOk" if fully_connected else "HealthBad")
        _repolish(self.connection_status_label)
        self._sync_right_panel(health_ok=ok, connected=fully_connected)

    def _on_connection_status_clicked(self) -> None:
        if getattr(self, "_llm_server_state", None) == "unreachable":
            asyncio.ensure_future(self._start_lm_studio_server_async())
        else:
            self._check_health()

    async def _start_lm_studio_server_async(self) -> None:
        self._append_log("Starting LM Studio server (lms server start)…", kind="system")
        try:
            proc = await asyncio.create_subprocess_exec(
                str(Path.home() / ".lmstudio" / "bin" / "lms"),
                "server", "start", "--port", "1234", "--bind", "0.0.0.0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output = (await proc.stdout.read()).decode(errors="replace") if proc.stdout else ""
            await proc.wait()
        except FileNotFoundError:
            self._on_error("lms CLI not found at ~/.lmstudio/bin/lms -- is LM Studio installed?")
            return
        if proc.returncode != 0:
            self._on_error(f"Could not start LM Studio server: {output.strip()[:300]}")
        self._check_health()

    def _sync_right_panel(self, *, health_ok: bool | None = None, connected: bool | None = None) -> None:
        if not hasattr(self, "right_panel"):
            return
        used, limit, is_real = self._context_usage()
        self.right_panel.set_token_usage(used, limit, is_real, self._tool_call_count)
        health = health_ok if health_ok is not None else self.overall_status_label.objectName() == "HealthOk"
        conn = connected if connected is not None else self.connection_status_label.objectName() == "HealthOk"
        workspace_text = self.workspace_status_label.text().removeprefix("Workspace: ")
        model_text = self.model_status_label.text().removeprefix("Model: ")
        self.right_panel.set_diagnostics(health, conn, workspace_text, model_text)

    def _check_workspace(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select workspace directory")
        if not path:
            return
        self._run_workspace_check(path)

    def _run_workspace_check(self, path: str) -> None:
        result = run_preflight(path)
        lines = [f"Workspace preflight for {path}", ""]
        for check in result.checks:
            marker = "✓" if check.passed else "✗"
            lines.append(f"{marker} {check.name}: {check.message}")
            if check.suggested_fix:
                lines.append(f"    fix: {check.suggested_fix}")
        # This preflight only checks host-side permissions; it does not by
        # itself give the agent access to `path` (there's no server API to
        # mount an arbitrary host directory into its sandbox -- see
        # workspace_server.py). Connecting the read-only folder tool here is
        # what actually does that.
        self._workspace_server.set_root(path)
        asyncio.ensure_future(self._register_workspace_mcp())
        lines.append("")
        lines.append(
            f"Connected {path!r} to the agent's list_folder/read_file tools "
            "(read-only, takes effect in the next new conversation)."
        )
        text = "\n".join(lines)
        self._append_log(text)
        self._workspace_full_text = f"{Path(path).name} ({'OK' if result.ok else 'issues found'})"
        self.workspace_value_label.setText(self._workspace_full_text)
        self.workspace_status_label.setText(f"Workspace: {Path(path).name}")
        self._sync_right_panel()
        box = QMessageBox(self)
        box.setWindowTitle("Workspace preflight")
        box.setIcon(QMessageBox.Icon.Information if result.ok else QMessageBox.Icon.Warning)
        box.setText(text)
        box.exec()

    # --- sending / conversation lifecycle --------------------------------------

    def _send(self) -> None:
        assert self._controller is not None
        if self._conversation_starting or self._model_switching:
            self._append_log("Please wait until the conversation and model are ready.", kind="error")
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.setPlainText("")
        self.stack.setCurrentWidget(self.log)
        self._append_log(text, kind="user")
        host_path = _find_host_path_reference(text)
        if host_path and not self._workspace_server.connected:
            try:
                host_candidate = Path(host_path).expanduser().resolve(strict=True)
            except OSError:
                host_candidate = None
            if host_candidate is not None and host_candidate.is_dir():
                self._workspace_server.set_root(str(host_candidate))
                asyncio.ensure_future(self._register_workspace_mcp())
                self._workspace_full_text = f"{host_candidate.name} (connected)"
                self.workspace_value_label.setText(self._workspace_full_text)
                self.workspace_status_label.setText(f"Workspace: {host_candidate.name}")
                self._sync_right_panel()
                self._append_log(
                    f"Connected host folder {host_candidate} for this desktop session. "
                    "The agent should use workspace_list_folder/workspace_read_file "
                    "instead of opening that /home path directly in its sandbox.",
                    kind="system",
                )
            else:
                self._append_log(
                    f"Heads up: \"{host_path}\" looks like a path on your computer. "
                    "The agent's sandbox (/workspace/project) can't see it directly -- "
                    "connect a folder via the Workspace button/icon first so the agent "
                    "can actually read it (workspace_connect_folder, "
                    "workspace_list_folder, workspace_read_file tools).",
                    kind="system",
                )
        elif host_path and self._workspace_server.connected:
            self._append_log(
                "Host workspace bridge is already connected. The agent should use "
                "workspace_list_folder/workspace_read_file for host files, not direct "
                "/home paths inside the sandbox.",
                kind="system",
            )
        self._auto_nudge_count_for_run = 0
        self._stuck_prompt_shown_for_run = False
        self._host_path_correction_sent_for_run = False
        self._silence_recovery_count_for_run = 0
        if self._controller.conversation_id is None:
            self._user_interrupted_awaiting_priority = False
            self._priority_enforcement_pending = False
            self._priority_enforcement_sent = False
            self._remember_user_message(text)
            profile_name = self.model_combo.currentData()
            model = self._selected_model()
            # None means "not decided yet" -- resolved in
            # _start_new_after_model_ready by classify_plan_or_code. Only
            # applies when the combo is still at its Code default; an
            # explicit manual Plan pick always wins over auto-decide.
            manual_agent_type = self.agent_type_combo.currentData()
            agent_type = (
                None
                if self._auto_decide_plan_code and manual_agent_type != "plan"
                else manual_agent_type
            )
            self._current_agent_type = agent_type
            self._pending_model = model
            self._pending_title = text[:80]
            self.agent_type_combo.setEnabled(False)  # fixed for the life of this conversation
            self._set_model_switching(True)
            asyncio.ensure_future(
                self._start_new_after_model_ready(
                    text=text,
                    profile_name=profile_name,
                    model=model,
                    agent_type=agent_type,
                )
            )
        else:
            self._remember_user_message(text)
            outgoing_text = text
            if self._user_interrupted_awaiting_priority:
                # Consumed once: interrupt() only cancels the in-flight LLM
                # call, it carries no explanation, so the very next message
                # is the only place left to tell the agent this supersedes
                # whatever it was doing instead of being just another queued
                # note to get back to once the old task is done.
                self._user_interrupted_awaiting_priority = False
                # Confirmed live 2026-08-01: a short question here (e.g.
                # "aký je problém" / "what's the problem") got treated as
                # "keep debugging to find the answer" -- the agent kept
                # calling more tools, running the SAME investigation it was
                # doing before Stop, instead of just answering from what it
                # already knew. Not ignoring the priority framing exactly,
                # but not doing what the person actually wanted either.
                # Spelled out explicitly now: answer from existing context
                # first, only use a tool if that's genuinely not enough.
                outgoing_text = (
                    "PRIORITY -- you were just interrupted. Do not resume or continue "
                    "the previous task. If this message is a question you can answer "
                    "from what you already know/did, answer it directly in plain text "
                    "-- do not call a tool just to keep investigating first. Only use "
                    "a tool if you genuinely cannot answer without one. Read and act "
                    "on this message first:\n\n" + text
                )
                # Enforcement layer for when the wording above still isn't
                # enough (confirmed live 2026-08-01: a real local model was
                # asked "ako to ide?" with this exact wording and still
                # called a tool -- curl -- instead of answering). Watched in
                # _render_event: the agent's very next action after this
                # message decides whether this escalates to a stronger,
                # one-shot forced follow-up, or clears quietly once a real
                # text answer arrives instead.
                self._priority_enforcement_pending = True
                self._priority_enforcement_sent = False
            elif self._last_run_state == RunState.RUNNING:
                # send_message only appends to the conversation's event
                # history -- it does NOT interrupt whatever LLM call is
                # already in flight, and whether the agent even acts on it
                # once it does see it depends entirely on the model
                # noticing and prioritizing it over what it was already
                # doing. Confirmed live 2026-07-31: a real local model kept
                # executing its original plan for multiple further steps
                # after being told "stop, wait for instructions" -- the
                # message was recorded almost instantly but had zero effect
                # on behavior. Use Stop (interrupt) first if it actually
                # needs to react now, not just a plain message.
                self._append_log(
                    "Agent is currently working -- this message is queued and will "
                    "only be picked up at its next step, not acted on immediately. "
                    "If you need it to react right now, click Stop first.",
                    kind="system",
                )
            self._controller.send_message(outgoing_text)

    async def _start_new_after_model_ready(
        self, *, text: str, profile_name: str | None, model: str | None, agent_type: str | None
    ) -> None:
        controller = self._controller
        ready = False
        try:
            if profile_name:
                await self._ensure_profile_ready(profile_name)
            ready = True
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not prepare the selected model: {exc}")
            self.input.setPlainText(text)
        finally:
            self._set_model_switching(False)
        if not ready or controller is not self._controller:
            return
        if agent_type is None:
            agent_type = await self._classify_plan_or_code(text, profile_name)
            index = self.agent_type_combo.findData(agent_type)
            if index >= 0:
                self.agent_type_combo.blockSignals(True)
                self.agent_type_combo.setCurrentIndex(index)
                self.agent_type_combo.blockSignals(False)
            self._current_agent_type = agent_type
            self._append_log(
                f"[Auto-decide: starting as {'Plan' if agent_type == 'plan' else 'Code'}]",
                kind="system",
            )
        self.agent_type_combo.setEnabled(False)
        profile = self._profiles_by_name.get(profile_name) if profile_name else None
        controller.start_new(
            llm_model=model,
            initial_message=text,
            agent_type=agent_type or "default",
            system_message_suffix=self._custom_instructions(agent_type),
        )

    async def _classify_plan_or_code(self, text: str, profile_name: str | None) -> str:
        profile = self._profiles_by_name.get(profile_name) if profile_name else None
        if profile is None or not profile.base_url:
            return "default"
        try:
            mode = await classify_plan_or_code(profile.base_url, profile.model, text)
        except Exception:  # noqa: BLE001 -- classification is a convenience, never blocks sending
            return "default"
        return "plan" if mode == "plan" else "default"

    # Confirmed live 2026-07-31: without this, a Plan-mode agent had to
    # figure out its own role and limits by trial and error mid-task (tried
    # invoke_skill("ssh") six times looking for a way to reach the network,
    # only later reasoning out loud "As a Planning Agent, my main job is to
    # CREATE THE PLAN... the code agent will execute curl requests"). Stating
    # this upfront instead of letting it discover it the hard way.
    _PLAN_MODE_NOTE = (
        "\n\n=== PLAN MODE -- READ THIS BEFORE YOUR FIRST TOOL CALL ===\n"
        "You are a Planning agent. Your ONLY output is a plan (PLAN.md). "
        "You do not implement, fix, install, run, download, or configure "
        "anything, ever, in this conversation. A separate Code agent, with "
        "full read/write and terminal access, executes your plan afterward "
        "-- that is a fact about this deployment, not a suggestion.\n\n"
        "Most important boundary: you must NOT create implementation artifacts "
        "inside the plan. Do not write complete scripts, source files, patches, "
        "diffs, command transcripts, generated configs, or copy-paste-ready "
        "code blocks. Do not say \"save this as ...\" or \"create this script\" "
        "followed by the script body. The Code agent writes all real code. In "
        "Plan mode you may name files/functions/classes, describe algorithms, "
        "list commands for Code to run later, and include small pseudocode only "
        "when it clarifies intent; keep it non-executable and clearly labeled "
        "as pseudocode.\n\n"
        "TOOLS YOU HAVE (this is the complete list -- nothing else is "
        "available no matter what a tool/skill's name or description "
        "implies): glob, grep, reading files, the planning file editor "
        "(PLAN.md only), think, finish, and workspace_connect_folder/"
        "list_folder/read_file for host paths the user mentions.\n\n"
        "YOU DO NOT HAVE, under any circumstance, in this mode: a terminal, "
        "shell/bash access, curl/wget/git/npm/pip or any other command, "
        "network access, the ability to write or edit any file except "
        "PLAN.md, or the ability to install/run/download/execute anything. "
        "This is a hard platform restriction, not a permission you could "
        "obtain by finding the right tool or skill.\n\n"
        "If you are tempted to reach for a skill (e.g. one named \"ssh\", "
        "\"deploy\", \"docker\", or similar) because it sounds like it could "
        "give you a missing capability: it cannot. Skills in Plan mode are "
        "documentation lookups only -- invoking one returns text, it does "
        "not grant you a terminal or network access, and calling it again, "
        "or calling a different skill, will not change that. If a skill "
        "call's result doesn't literally give you file/plan content to read "
        "or write, stop -- you are not going to get a different outcome by "
        "retrying or trying another skill. This exact mistake has happened "
        "before (an agent with no terminal called the \"ssh\" skill six "
        "times in a row hoping a different result would appear -- it never "
        "does, the skill only returns static documentation).\n\n"
        "The correct move whenever you find yourself wanting to actually DO "
        "something (run a command, check something live, install a "
        "dependency, test an API) is always the same: do not attempt it, do "
        "not search for a way to attempt it -- write the concrete step into "
        "PLAN.md for the Code agent to execute later (e.g. \"run `curl -s "
        "https://api.example.com/schema` to confirm the response shape\"). "
        "That is a complete, correct action in Plan mode, not a fallback.\n\n"
        "Required PLAN.md shape: short goal, current facts discovered from "
        "reading, ordered implementation steps for Code, files likely touched, "
        "verification commands Code must run, risks/open questions, and a final "
        "handoff note. If the user asks you to make a script while in Plan mode, "
        "do not write the script; write a plan step telling Code exactly what "
        "script to create and what behavior it must have.\n\n"
        "You do NOT have launch_subagent/sub-agent delegation in this mode "
        "(confirmed live -- it is not in your tool list) -- do not search "
        "for it or try to work around its absence. If the task involves a "
        "private local project, write into PLAN.md that Code should use a "
        "matching project subagent only if its exact name appears in the "
        "available agent types. "
        "\"project-module-engineer\" is only a subagent_type value for a "
        "future launch_subagent call when registered, NOT a directory, file, "
        "or project name -- do not search the filesystem for it. If it is "
        "not registered, the plan must allow Code to use an available coder/"
        "general-purpose agent or implement the work directly."
    )

    _CODE_MODE_NOTE = (
        "\n\n=== CODE MODE -- READ THIS BEFORE YOUR FIRST TOOL CALL ===\n"
        "You are a Code agent. Your job is to actually IMPLEMENT the "
        "change -- edit real files, run real commands, and verify the "
        "result actually works.\n\n"
        "TOOLS YOU HAVE: a terminal (Bash/shell commands, installing "
        "dependencies, running tests/builds, git), the file editor "
        "(create/edit/view files), browser tools, a task tracker, "
        "switch_llm, think, finish, and workspace_connect_folder/"
        "list_folder/read_file/write_file for host paths the user mentions "
        "(write_file needs a separate confirmation per call).\n\n"
        "Before calling finish: confirm the actual change exists on disk "
        "(read the file back, or run the test/build) -- do not call finish "
        "based on believing a previous step succeeded without checking.\n\n"
        "Subagent delegation for a private local project: inspect the agent "
        "types exposed by the task/launch_subagent tool before delegating. "
        "Call subagent_type=\"project-module-engineer\" only when that exact "
        "type is listed as available. Never submit an invented or unavailable "
        "subagent type. If the specialist is absent, use a listed coder or "
        "general-purpose type when appropriate, otherwise implement the work "
        "directly with your own Code tools. Do not search the filesystem for "
        "a path named \"project-module-engineer\"; it is only an optional "
        "registered agent type."
    )

    # Only appended when this Code conversation was actually started via
    # Continue-as-Code from a Plan conversation -- appending it to a plain,
    # from-scratch Code conversation would be actively wrong (there is no
    # PLAN.md, nothing "already happened in a separate Plan conversation").
    _CODE_MODE_CONTINUED_FROM_PLAN_NOTE = (
        "\n\nTHIS CONVERSATION WAS CONTINUED FROM A PLAN (you were told to "
        "continue implementing a plan). Describing what should be done, or "
        "producing another plan, is not an acceptable outcome here; that "
        "already happened in the separate Plan conversation before this "
        "one.\n\n"
        "Your sandbox is a brand-new, separate container from the one the "
        "Plan agent used -- it does NOT inherit whatever host folder the "
        "Plan agent had connected. Concretely, this means "
        "/workspace/project will be EMPTY except for .agents_tmp/PLAN.md "
        "and a bare, commit-less .git -- that is expected, not an error, "
        "and not a sign the project is missing. Do these two things, in "
        "order, before writing any code:\n"
        "  1. Read .agents_tmp/PLAN.md in full if you haven't already -- "
        "it already has the objective, approach, and concrete steps. Do "
        "not re-derive or second-guess it into a new plan.\n"
        "  2. Call workspace_connect_folder with the real host project "
        "path (stated in this message, or in the plan/earlier conversation "
        "if not) so you can actually read and modify the real files. An "
        "empty /workspace/project is a signal to connect the folder, not "
        "a signal to explore unrelated things (curl, browser, guessing at "
        "URLs) looking for something to do -- that has happened before and "
        "wasted an entire run without producing any real change.\n\n"
        "If the plan includes discovery steps it explicitly deferred to "
        "you (e.g. \"run curl against X to confirm the response shape\"), "
        "treat those as genuinely unverified -- run them for real rather "
        "than assuming the plan's assumptions were correct.\n\n"
        "Override for this conversation specifically: your base "
        "instructions elsewhere say that when you hit a major issue while "
        "executing a plan, you should \"propose a new plan and confirm "
        "with the user\" instead of working around it. That does NOT apply "
        "here. You already have a concrete, user-approved plan in "
        ".agents_tmp/PLAN.md -- an empty /workspace/project, a missing "
        "dependency, or a step that needs adjusting is an implementation "
        "detail to solve and keep going (reconnect the folder, install the "
        "dependency, adapt the step), not a reason to stop and write "
        "another plan. Only stop and ask (via ask_user_question, not by "
        "drafting a new plan document) if the issue reveals the plan's "
        "actual approach is wrong at a level only the user can decide -- "
        "not for ordinary setup/implementation friction."
    )

    _AGENT_PROGRESS_NOTE = (
        "\n\nProgress updates: While working, send short agent-style progress "
        "messages before meaningful phases and after important findings. Keep "
        "them concrete: what you are checking, what you found, what you will "
        "change or verify next. Do not narrate obvious internal thoughts, do "
        "not apologize repeatedly, and do not wait silently through long tool "
        "work. For longer tasks, update roughly every 20-40 seconds or when "
        "the work phase changes. These updates should sound like a working "
        "coding agent, not a casual chatbot."
    )

    def _custom_instructions(self, agent_type: str | None = None, *, continued_from_plan: bool = False) -> str | None:
        """Custom agent instructions, passed per-conversation.

        Settings -> Agent writes these to
        `agent_settings.agent_context.system_message_suffix`, which the
        app-server stores but never applies: it rebuilds AgentContext from
        the *start request* and drops the stored one (verified live
        2026-07-29 -- text saved there reached neither the system prompt nor
        the dynamic context). So the settings field is treated purely as
        storage here, and the value is delivered through the start request,
        which does work.
        """
        text = (self._custom_instructions_text or "") + self._AGENT_PROGRESS_NOTE
        if agent_type == "plan":
            text = text + self._PLAN_MODE_NOTE
        elif agent_type == "default":
            text = text + self._CODE_MODE_NOTE
            if continued_from_plan:
                text = text + self._CODE_MODE_CONTINUED_FROM_PLAN_NOTE
        return text or None

    async def _load_custom_instructions(self) -> None:
        try:
            settings = await self._client.get_settings()
        except Exception:  # noqa: BLE001 -- non-fatal; conversations still start
            return
        agent_context = (settings.get("agent_settings") or {}).get("agent_context") or {}
        self._custom_instructions_text = (agent_context.get("system_message_suffix") or "").strip()

    def _remember_user_message(self, text: str) -> None:
        normalized = text.strip()
        if not normalized:
            return
        if self._recent_user_messages and self._recent_user_messages[-1] == normalized:
            return
        self._recent_user_messages.append(normalized)
        self._recent_user_messages = self._recent_user_messages[-5:]

    def _pause_agent(self) -> None:
        import traceback

        print("\n=== _pause_agent CALLED ===", flush=True)
        traceback.print_stack()

        if self._controller is not None:
            self._controller.pause()
            self._append_log(
                "Pause requested. The current model response may finish first.",
                kind="system",
            )

    def _interrupt(self) -> None:
        if self._controller is not None:
            self._controller.interrupt()
            self._user_interrupted_awaiting_priority = True
            self._append_log("Agent interrupted after holding Stop for 5 seconds.", kind="system")

    def _on_conversation_ready(self, conversation_id: str) -> None:
        self.changes_btn.setEnabled(True)
        self.mempalace_btn.setEnabled(True)
        self.browser_preview_btn.setEnabled(True)
        self._conversation_started_at = datetime.now()
        self._duration_frozen = False
        self._update_duration_label()
        if self._is_resuming:
            # Reattaching, not creating -- don't touch the history record's
            # title/model. _on_state_changed will update its status shortly
            # once status polling picks up the real current state.
            self._is_resuming = False
            return
        if self._continuing_from_plan_id is not None:
            # Continue-as-Code's new conversation_id is a different backend
            # id (parent_conversation_id links it to the Plan one) -- swap
            # the Plan row's id in place instead of inserting a second row,
            # so the sidebar shows one entry for the whole task instead of
            # two that both end up showing the same thing.
            plan_id = self._continuing_from_plan_id
            self._continuing_from_plan_id = None
            asyncio.ensure_future(self._replace_history_id_and_refresh(plan_id, conversation_id))
            return
        asyncio.ensure_future(self._record_and_refresh(conversation_id))

    async def _record_and_refresh(self, conversation_id: str) -> None:
        await self._history.record_new(
            conversation_id, llm_model=self._pending_model, title=self._pending_title
        )
        await self._refresh_sidebar_history_async()

    async def _write_history_status(self, conversation_id: str, status: str) -> None:
        async with self._history_write_lock:
            await self._history.update_status(conversation_id, status)

    async def _replace_history_id_and_refresh(self, old_conversation_id: str, new_conversation_id: str) -> None:
        await self._history.replace_id(
            old_conversation_id, new_conversation_id, llm_model=self._pending_model, title=self._pending_title
        )
        await self._refresh_sidebar_history_async()

    _TERMINAL_STATES = (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED, RunState.ERROR, RunState.STUCK)

    def _update_duration_label(self) -> None:
        if self._conversation_started_at is None or self._duration_frozen:
            return
        elapsed = int((datetime.now() - self._conversation_started_at).total_seconds())
        minutes, seconds = divmod(elapsed, 60)
        hours, minutes = divmod(minutes, 60)
        text = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
        self.duration_label.setText(text)

    def _recheck_state_after_grace(self, raw_state: RunState, generation: int) -> None:
        # generation guards against a real state_changed (override or not)
        # having already landed since this was scheduled -- without it, a
        # long quiet stretch inside a conversation that later became
        # genuinely, normally RUNNING (a real emission, not an override)
        # could wrongly get yanked back down to this timer's stale captured
        # raw_state (e.g. an old ERROR/Waiting from many minutes earlier).
        if generation != self._state_generation:
            return
        if self._controller is None or self._last_run_state != RunState.RUNNING:
            return  # a real state_changed already landed and moved things on
        if self._last_event_at is not None:
            elapsed = (datetime.now() - self._last_event_at).total_seconds()
            if elapsed < _RECENT_ACTIVITY_GRACE_S:
                return  # newer activity arrived; its own override already rescheduled this
        self._on_state_changed(raw_state)

    def _on_state_changed(self, state: RunState) -> None:
        self._state_generation += 1
        generation = self._state_generation
        raw_state = state
        if state in (RunState.ERROR, RunState.FINISHED_UNVERIFIED) and self._last_event_at is not None:
            elapsed = (datetime.now() - self._last_event_at).total_seconds()
            if elapsed < _RECENT_ACTIVITY_GRACE_S:
                state = RunState.RUNNING
                # ConversationController._poll_status only emits
                # state_changed when the RESOLVED state actually changes
                # from what it last saw -- it has no idea this override
                # happened here, purely in the view layer. If the
                # conversation has genuinely finished (nothing more ever
                # arrives), that resolved value never changes again, so
                # the controller never emits again either, and without
                # this timer the override above would show "Running…"
                # forever with no way to ever correct itself (confirmed
                # live 2026-07-31: stuck at "Running… 14m 27s" long after
                # the agent's actual completion message). Re-evaluate for
                # real once the grace window has actually elapsed.
                remaining_ms = int((_RECENT_ACTIVITY_GRACE_S - elapsed) * 1000) + 500
                QTimer.singleShot(
                    remaining_ms, lambda: self._recheck_state_after_grace(raw_state, generation)
                )
        self._last_run_state = state
        if state == RunState.ERROR:
            self._notify_needs_attention()
        if state in self._TERMINAL_STATES or state == RunState.PAUSED:
            # Freeze instead of continuing to tick through idle time after
            # the agent is actually done -- "how long this ran", not "how
            # long since it started including time spent waiting". PAUSED
            # is included even though it isn't terminal: confirmed live
            # 2026-08-01 that clicking Stop (interrupt) leaves the timer
            # ticking exactly like a still-running task, with nothing in
            # the UI to tell the two apart -- the whole point of clicking
            # Stop is to be sure it actually stopped.
            self._update_duration_label()
            self._duration_frozen = True
        elif self._duration_frozen and state == RunState.RUNNING:
            self._duration_frozen = False
        # Plan -> Act handoff (borrowed from Claude Code's plan mode / Roo
        # Code's Orchestrator): once a Plan conversation reaches a terminal
        # state, offer to hand its output to a fresh Code conversation
        # instead of the user copying it over by hand. Computed before the
        # label below so it can override what gets shown -- confirmed live
        # 2026-07-31: showing "Finished (unverified)" here was misleading,
        # since the Plan conversation isn't actually done, it's waiting to
        # hand off to Code (and reads as a separate, unrelated, finished
        # conversation in the sidebar rather than one step of one task).
        current_conversation_id = self._controller.conversation_id if self._controller is not None else None
        # Continue-as-Code already having run for this conversation is only
        # tracked in memory (_current_agent_type/_plan_waiting_conversation_id)
        # -- lost on reattach (e.g. after an app restart), which then showed
        # plain "Finished" for a Plan conversation that had *already* been
        # handed off, instead of either "Waiting" or anything reflecting
        # that reality (confirmed live 2026-07-31). The server's own
        # sub_conversation_ids is durable and authoritative for "has this
        # already been continued", independent of what this window
        # happens to remember.
        already_continued = bool(self._controller.sub_conversation_ids) if self._controller is not None else False
        plan_finished = (
            not already_continued
            and (self._current_agent_type == "plan" or current_conversation_id == self._plan_waiting_conversation_id)
            and state in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED)
        )
        if plan_finished:
            self._plan_waiting_conversation_id = current_conversation_id
        if plan_finished:
            label, style_name = "Waiting to continue as Code", "StateWarning"
        elif already_continued and state in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED):
            label, style_name = "Continued as Code", "StateWarning"
        else:
            label, style_name = STATE_LABELS.get(state, (str(state), ""))
        self.state_label.setText(label)
        self.state_label.setObjectName(style_name)
        self.state_label.setToolTip(
            "The plan is done, but the task isn't -- click Continue as Code (or "
            "enable Auto-continue) to hand it off and actually implement it."
            if plan_finished
            else (
                "A Code agent was already started from this plan (open it from "
                "Conversations/Mission Control to see its progress)."
                if already_continued and state in (RunState.COMPLETED, RunState.FINISHED_UNVERIFIED)
                else (
                    "Agent finished without an explicit finish confirmation from the model."
                    if state == RunState.FINISHED_UNVERIFIED
                    else ""
                )
            )
        )
        _repolish(self.state_label)
        if self._controller is not None and self._controller.conversation_id is not None:
            asyncio.ensure_future(
                self._write_history_status(
                    self._controller.conversation_id,
                    "WAITING_TO_CONTINUE" if plan_finished else state.name,
                )
            )
        self.continue_as_code_btn.setVisible(plan_finished)
        if plan_finished and self.auto_continue_as_code_check.isChecked() and not self._auto_continue_triggered:
            self._auto_continue_triggered = True
            self._continue_as_code()
        # Community-reported Qwen A3B/27B failure mode (2026-07-28 research):
        # the model can get stuck re-trying the same search/reasoning instead
        # of making progress. The server's own stuck-detector already
        # surfaces this as RunState.STUCK. Happens often enough in practice
        # that waiting for a manual click every time was more friction than
        # the false-positive risk justified -- auto-send the nudge, but cap
        # it per run so a genuinely broken loop still falls back to the
        # manual button instead of nudging forever.
        if state == RunState.STUCK:
            if self._auto_supervise:
                # ConversationWatchdog already interrupts and nudges/stops on
                # its own, proactively, from hash-dedup on the live event
                # stream -- by the time the SDK's own (reactive, less
                # configurable) stuck detector fires this state, the
                # watchdog has already handled it. Firing this ad-hoc nudge
                # too would double up on the same stuck run.
                pass
            elif self._auto_nudge_count_for_run < _MAX_AUTO_NUDGES_PER_RUN and not self._auto_nudge_in_flight:
                self._auto_nudge_count_for_run += 1
                self.stuck_nudge_btn.setVisible(False)
                asyncio.ensure_future(self._auto_interrupt_and_nudge())
            elif not self._auto_nudge_in_flight:
                # Out of auto-attempts -- leave it to the user, but make
                # sure they actually notice instead of a button quietly
                # appearing on a window they might not be looking at.
                self.stuck_nudge_btn.setVisible(True)
                if not self._stuck_prompt_shown_for_run:
                    self._stuck_prompt_shown_for_run = True
                    asyncio.ensure_future(
                        self._handle_stuck(
                            "The agent looks stuck (repeating the same searches/reasoning) "
                            f"and already used its {_MAX_AUTO_NUDGES_PER_RUN} auto-nudge attempt(s)."
                        )
                    )
        else:
            self.stuck_nudge_btn.setVisible(False)

    def _on_watchdog_step(self, event: dict) -> None:
        """ConversationWatchdog only reports the events worth surfacing --
        action/observation/state are already visible via the normal
        events_received -> log path, so only nudge/stuck (and error, which
        _on_error already handles) are logged here."""
        kind = event.get("kind")
        if kind == "nudge":
            self._append_log(
                f"[Auto-supervise: repeated tool call ({event.get('tool')}) -- "
                f"nudging, attempt {event.get('attempt')}/{event.get('of')}]",
                kind="system",
            )
        elif kind == "stuck":
            # kind="error" here, not "system" -- confirmed live 2026-07-31:
            # this is exactly the "gave up, needs a human" moment, and
            # "system" notes fold into the collapsed Working card where
            # nobody sees them without expanding it first.
            reason = event.get("reason", "")
            self._append_log(f"Auto-supervise stopped the agent: {reason}", kind="error")
            # Confirmed live 2026-08-01: this genuinely needs a human (the
            # agent gave up, sits paused indefinitely otherwise -- see
            # _handle_stuck), but nothing here was actually telling anyone
            # it happened; a conversation could sit stuck for hours with no
            # visible sign. Same severity as RunState.ERROR, same response.
            self._notify_needs_attention()
            asyncio.ensure_future(self._handle_stuck(reason))
        elif kind == "progress":
            # Updates the Working card's own visible header (see
            # LogView.note_progress) rather than adding a hidden note --
            # this needs to be seen without expanding anything, which is
            # the whole point.
            self.log.note_progress(f"Still working… ({event.get('steps')} steps, making progress)")

    _NUDGE_TEXT = (
        "You seem to be stuck repeating the same searches or reasoning without making "
        "progress. Stop searching further -- directly open and read the specific file "
        "most relevant to the task, then continue from there."
    )

    async def _auto_interrupt_and_nudge(self) -> None:
        if self._controller is None:
            return
        self._auto_nudge_in_flight = True
        try:
            self._controller.interrupt()
            # /interrupt is "verified live to take effect in ~1s" (see
            # ConversationController.interrupt's docstring) -- sending the
            # follow-up before that lands would race the in-flight call.
            await asyncio.sleep(1.5)
            self._append_log(
                f"[auto-nudge {self._auto_nudge_count_for_run}/{_MAX_AUTO_NUDGES_PER_RUN}: "
                "agent looked stuck, interrupting and redirecting]",
                kind="system",
            )
            self._append_log(self._NUDGE_TEXT, kind="system")
            self._controller.send_message(self._NUDGE_TEXT)
        finally:
            self._auto_nudge_in_flight = False

    def _flash_if_unfocused(self) -> None:
        """Lighter-weight cousin of _notify_needs_attention: flashes the
        taskbar/dock icon without stealing focus or raising the window.

        Added for the second-window feature -- with two windows open, real
        agent replies in the one you're not looking at were easy to miss
        entirely (it just keeps going quietly in the background); a full
        _notify_needs_attention() would be too disruptive here since it
        forces the window to the front, yanking focus away from whatever
        the user is actually doing in the other one. This just flashes."""
        if not self.isActiveWindow():
            QApplication.alert(self)

    def _notify_needs_attention(self) -> None:
        """Forces the window to the front and flashes the taskbar icon --
        used whenever the agent stops/errors and genuinely needs the user's
        attention. Confirmed live 2026-07-31: a collapsed Working card and
        an unfocused window both hide this otherwise -- nothing short of
        actually grabbing focus reliably gets noticed."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        QApplication.alert(self)

    _HOST_PATH_PREFIXES = ("/home/", "/Users/", "/root/", "/mnt/", "/media/")

    def _correct_host_path_confusion_if_needed(self, tool_name: str | None, error_text: str) -> None:
        """Confirmed live 2026-08-01: an agent correctly used
        workspace_list_folder on a host path the user gave it, then later
        in the SAME task switched to plain glob on that same host path
        (which only ever sees /workspace/project), got "not a valid
        directory" every time, and looped several retries before the
        watchdog stopped it. Waiting for a repeat before correcting is too
        slow -- fixes it after the very first occurrence, per direct
        request ("Po prvej chybe rovnaký chybný call neopakovať" -- don't
        repeat the same failing call after the first error). Queues a
        plain message rather than interrupting -- the agent picks it up at
        its own next step, no risk of cutting off an in-flight call that
        might have already moved on by itself.
        """
        if self._host_path_correction_sent_for_run:
            return
        if tool_name not in ("glob", "grep", "file_editor", "terminal"):
            return
        if self._controller is None or self._controller.conversation_id is None:
            return
        lowered = error_text.lower()
        path_error = (
            "not a valid directory" in lowered
            or "no such file or directory" in lowered
            or "invalid `path` parameter" in lowered
            or "invalid path parameter" in lowered
            or "please provide a valid path" in lowered
        )
        if not path_error:
            return
        if not any(prefix in error_text for prefix in self._HOST_PATH_PREFIXES):
            return
        self._host_path_correction_sent_for_run = True
        text = (
            f"That {tool_name} call failed because it was pointed at a host "
            "filesystem path -- this tool only ever sees /workspace/project, "
            "it cannot reach a path like the one you just tried, and "
            "retrying it verbatim will fail the same way every time. Use "
            "workspace_connect_folder on that exact host path, then "
            "workspace_list_folder/workspace_read_file to browse it instead."
        )
        self._append_log(text, kind="system")
        self._controller.send_message(text)

    _SILENCE_TIMEOUT_S = 250
    _MAX_SILENCE_RECOVERIES_PER_RUN = 3

    def _check_conversation_silence(self) -> None:
        """Confirmed live 2026-08-01: an OpenHands SDK response_dispatch
        warning ("LLM response contained no tool call and no content --
        sending corrective feedback") was followed by total silence --
        execution_status stayed "running" forever, LM Studio went idle, and
        neither the app's own status poll (server genuinely still reports
        "running", nothing to correct) nor ConversationWatchdog (only ever
        looks at ActionEvents -- if literally nothing happens, it has
        nothing to compare and never fires) ever notices. Runs on the same
        15s timer as the model-sync check; independent of both of those.

        Capped at _MAX_SILENCE_RECOVERIES_PER_RUN, not a single one-shot --
        confirmed live 2026-08-01 that a one-shot version left a
        conversation permanently stuck with no further help after the
        first nudge didn't get a real response either (LM Studio showed
        "GENERATING" for 4+ minutes with zero new events -- a genuinely
        very slow/wedged local generation, not a quick corrective-feedback
        blip the first nudge is meant for). _last_event_at naturally moves
        forward once the interrupt/redirect below produce their own
        InterruptEvent/MessageEvent, so each recovery attempt still needs a
        fresh _SILENCE_TIMEOUT_S of real silence before the next one fires
        -- this isn't a tight retry loop.
        """
        if self._silence_recovery_count_for_run >= self._MAX_SILENCE_RECOVERIES_PER_RUN:
            return
        if self._controller is None or self._controller.conversation_id is None:
            return
        if self._last_run_state != RunState.RUNNING:
            return
        if self._pending_task_action:
            # A "task" (launch_subagent) call is in flight -- it blocks
            # until the sub-agent's own run completes, and none of that
            # sub-agent's intermediate steps reach this conversation's event
            # stream, so long silence here is expected, not a sign anything
            # is stuck. Confirmed live: interrupting mid-delegation (the
            # nudge below) tore the delegation down instead of just nudging
            # an idle model -- exactly the "sometimes it just disconnects"
            # symptom reported for project-module-engineer. Let it run; the
            # user can still Stop manually if it's genuinely wedged.
            return
        if self._last_event_at is None:
            return
        elapsed = (datetime.now() - self._last_event_at).total_seconds()
        if elapsed < self._SILENCE_TIMEOUT_S:
            return
        self._silence_recovery_count_for_run += 1
        asyncio.ensure_future(self._handle_silence(elapsed))

    async def _handle_silence(self, elapsed_s: float) -> None:
        # kind="system", not "error": this is the app's normal recovery
        # path working as intended, not a problem for the user to act on --
        # confirmed live 2026-08-01 it correctly unstuck a real hang on its
        # own (interrupt, redirect, agent picked back up within seconds).
        # Styling it as an error every time made a routine self-recovery
        # look like something had gone wrong.
        self._append_log(
            f"No response from the model for over {int(elapsed_s)}s -- nudging it to "
            f"continue (attempt {self._silence_recovery_count_for_run}/"
            f"{self._MAX_SILENCE_RECOVERIES_PER_RUN}).",
            kind="system",
        )
        if self._controller is None or self._controller.conversation_id is None:
            return
        self._controller.interrupt()
        await asyncio.sleep(1.5)  # see _auto_interrupt_and_nudge's docstring for why this delay matters
        text = (
            "Your last response appears to have come back empty (no tool call, no "
            "text). Please try again: call a tool to make progress, or if you're "
            "genuinely stuck, call ask_user_question with concrete options."
        )
        # kind="system", not "user": sent by the app, not typed by the
        # person -- displaying it under a "You" bubble misrepresented who
        # actually said it (confirmed live, screenshot showed it rendered
        # exactly like a real user chat message). The text still goes to
        # the agent unchanged via send_message below; only the local
        # display styling changes.
        self._append_log(text, kind="system")
        self._controller.send_message(text)
        if self._silence_recovery_count_for_run >= self._MAX_SILENCE_RECOVERIES_PER_RUN:
            # Last automatic attempt used up -- confirmed live 2026-08-01
            # this can genuinely happen (LM Studio showed "GENERATING" for
            # 4+ minutes straight with zero new events). Unlike the routine
            # nudge above, this one needs the user's attention: nothing
            # further will happen on its own if this attempt doesn't help
            # either.
            self._append_log(
                "Still no response after 3 automatic nudge attempts -- this needs a "
                "look. Click Stop to interrupt manually, or check if the model server "
                "itself is stuck.",
                kind="error",
            )
            self._notify_needs_attention()

    async def _send_forced_priority_followup(self) -> None:
        """One-shot enforcement for when the priority-message wording alone
        isn't enough (confirmed live 2026-08-01: a real local model was
        asked a plain question right after Stop and still called a tool --
        curl -- instead of answering it directly). Fires exactly once per
        priority message (see _priority_enforcement_sent) so a model that
        still won't comply doesn't get stuck in an interrupt/retry loop --
        it falls through to whatever it does next after this."""
        if self._controller is None or self._controller.conversation_id is None:
            return
        self._controller.interrupt()
        await asyncio.sleep(1.5)  # see _auto_interrupt_and_nudge's docstring for why this delay matters
        text = (
            "STOP. You just called a tool instead of answering the priority "
            "question directly -- that instruction was not optional. Do not "
            "call any more tools. Write your answer as plain text right now, "
            "based only on what you already know."
        )
        self._append_log(text, kind="system")
        self._controller.send_message(text)

    async def _handle_stuck(self, reason: str) -> None:
        """Runs after ConversationWatchdog gives up. Confirmed live
        2026-07-31: asking the user to decide *immediately* sometimes raced
        an already-in-flight auto-nudge that was about to get the agent
        moving again on its own, making the question pointless -- so this
        waits and only actually does anything if the conversation is still
        not running a few seconds later.

        Doesn't ask the user directly, and doesn't invent generic options
        itself either -- redirects the agent to propose its own concrete,
        situation-specific options via the ask_user_question tool it
        already has (same as how Claude Code's own AskUserQuestion works),
        since the agent has the actual context on what alternatives make
        sense here and the app doesn't.
        """
        await asyncio.sleep(6.0)
        if self._controller is None or self._controller.conversation_id is None:
            return
        if self._last_run_state == RunState.RUNNING:
            return  # it recovered on its own -- nothing to do
        self._controller.interrupt()
        await asyncio.sleep(1.5)  # see _auto_interrupt_and_nudge's docstring for why this delay matters
        text = (
            f"{reason} Do not repeat what you already tried. Call ask_user_question "
            "with 2-4 concrete, genuinely different options for how to proceed from "
            "here, specific to this task -- let the user pick the direction instead "
            "of guessing again."
        )
        self._append_log(text, kind="system")
        self._controller.send_message(text)
        self._stuck_prompt_shown_for_run = False

    def _retry_last_message(self) -> None:
        """Error card's Retry button: resend the last user message, same
        mechanism the auto-nudge uses (ConversationController.send_message)."""
        if self._controller is None or not self._recent_user_messages:
            return
        text = self._recent_user_messages[-1]
        self._append_log(text, kind="user")
        self._controller.send_message(text)

    def _interrupt_and_nudge(self) -> None:
        if self._controller is None:
            return
        self._controller.interrupt()
        self.stuck_nudge_btn.setVisible(False)
        self.input.setPlainText(self._NUDGE_TEXT)
        self.input.setFocus()

    def _on_auto_continue_as_code_toggled(self, checked: bool) -> None:
        self._settings.setValue("auto_continue_as_code", checked)

    def _continue_as_code(self) -> None:
        asyncio.ensure_future(self._continue_as_code_async())

    async def _continue_as_code_async(self) -> None:
        # 2026-07-31: reverted the brand-new-window approach (it caused a
        # real bug -- closing the Plan window while a secondary window was
        # still mid-setup unloaded the model out from under it -- and the
        # user explicitly didn't want a second app window appearing at
        # all). Back to reusing this window in place; the state_label reset
        # to "Starting…" below is what actually fixed the original stale-
        # "Finished (unverified)" symptom, independent of the window count.
        if self._controller is None or self._controller.conversation_id is None:
            return
        parent_id = self._controller.conversation_id
        # The Plan conversation's sidebar entry was left showing
        # "WAITING_TO_CONTINUE" forever once Code actually started --
        # nothing ever moved it off that status. Continuing means it's
        # running now (as the Code conversation, which is the whole point
        # of "waiting to continue"), so reflect that immediately rather
        # than only updating the *new* conversation's own history entry.
        asyncio.ensure_future(self._write_history_status(parent_id, RunState.RUNNING.name))
        # Disconnect + stop the OLD (Plan) controller now, before the
        # potentially several-second-long model-switch await below -- its
        # status-polling loop was still live during that gap and could
        # deliver one more stray state_changed(FINISHED) for parent_id,
        # overwriting the RUNNING write above and leaving the sidebar
        # stuck showing "Finished" even while the Code agent kept working
        # (confirmed live 2026-07-31). _new_controller() disconnects it.
        self._continuing_from_plan_id = parent_id
        self._new_controller()
        default_code_model = self._settings.value("default_code_model_name")
        if default_code_model:
            self.model_combo.blockSignals(True)
            self._switch_to_named_model(default_code_model)
            self.model_combo.blockSignals(False)
            self._set_model_switching(True)
            try:
                await self._ensure_profile_ready(default_code_model)
            except Exception as exc:  # noqa: BLE001
                self._on_error(f"Could not load the Code model: {exc}")
                return
            finally:
                self._set_model_switching(False)
        self._current_agent_type = "default"
        self.continue_as_code_btn.setVisible(False)
        self._tool_call_count = 0
        self._estimated_context_chars = 0
        self._real_used_tokens = 0
        self._real_context_window = 0
        self._auto_compact_triggered = False
        self._update_tool_call_label()
        # Without this, the status pill keeps showing the parent conversation's
        # terminal state ("Finished (unverified)") until the new conversation's
        # own first state_changed event arrives -- which looks exactly like the
        # new conversation is stuck, even while it's actively running.
        self.state_label.setText("Starting…")
        self.state_label.setObjectName("")
        self.state_label.setToolTip("")
        _repolish(self.state_label)
        self.log.clear()
        self.stack.setCurrentWidget(self.log)
        self._append_log(f"[continuing plan from conversation {parent_id} as a Code agent…]")
        self.agent_type_combo.setCurrentIndex(self.agent_type_combo.findData("default"))
        self.agent_type_combo.setEnabled(False)
        model = self._selected_model()
        self.agent_type_combo.setEnabled(False)
        self._pending_model = model
        self._pending_title = "Continued from plan"
        profile_name = self.model_combo.currentData()
        profile = self._profiles_by_name.get(profile_name) if profile_name else None
        self._controller.start_new(
            llm_model=model,
            initial_message=self._continue_as_code_initial_message(),
            agent_type="default",
            parent_conversation_id=self._continuing_from_plan_id,
            system_message_suffix=self._custom_instructions("default", continued_from_plan=True),
        )

    def _continue_as_code_initial_message(self) -> str:
        """Confirmed live 2026-07-31: the Code sandbox is a brand-new,
        separate container -- it does NOT inherit the Plan sandbox's
        connected host folder. A Code agent that never re-runs
        workspace_connect_folder ends up with an empty /workspace/project
        (just its own untouched .agents_tmp/PLAN.md and a bare, commit-less
        .git) and, finding nothing real to work with, wandered off into
        curl/browser exploration instead of implementing anything -- even
        though it HAD already found and read PLAN.md by that point. Spelling
        out both steps explicitly (read the plan, then reconnect the real
        project folder) closes that gap instead of assuming the agent will
        infer it from an empty directory.
        """
        text = (
            "Continue implementing the plan from the previous conversation. "
            "First, read .agents_tmp/PLAN.md in full -- it already contains "
            "the objective, approach, and implementation steps; do not "
            "recreate or re-derive it. "
        )
        root = self._workspace_server.root_display if self._workspace_server.connected else ""
        if root:
            text += (
                f"Second, call workspace_connect_folder with \"{root}\" -- "
                "this is a new sandbox and does not have that folder "
                "connected yet, even though the plan was written from it; "
                "/workspace/project itself is empty except for the plan "
                "file. Then implement the plan's steps against the real "
                "project via the workspace_* tools (or by copying the "
                "relevant files into /workspace/project first, if you need "
                "the terminal/file_editor tools directly on them)."
            )
        else:
            text += (
                "No host folder was connected during planning -- if the "
                "plan references files outside /workspace/project, connect "
                "the right folder with workspace_connect_folder first."
            )
        return text

    # --- history / sidebar -------------------------------------------------------

    async def _init_history_and_refresh(self) -> None:
        await self._history.init()
        await self._refresh_sidebar_history_async()
        await self._presets.init()
        await self._reload_presets_async()
        if self._owns_mcp_servers:
            # A second window's servers are the SAME already-started
            # objects (see __init__) -- calling .start() again would try
            # to bind their fixed ports a second time and fail, since the
            # owning window's uvicorn instance is still listening on them.
            await self._start_ask_user_server()
            await self._start_workspace_server()
        await self._load_custom_instructions()

    def _current_mcp_config(self) -> dict:
        """Built directly from this app's own already-running local MCP
        servers. The current app-server path registers this globally via
        /api/v1/settings before conversations start; this helper keeps the
        desired shape in one place for registration/debug use.
        """
        config: dict = {}
        if self._ask_user_server is not None:
            config[_ASK_USER_MCP_NAME] = {
                "url": self._ask_user_server.mcp_url,
                "transport": "streamable-http",
                "timeout": _ASK_USER_TIMEOUT_S,
                "sse_read_timeout": _ASK_USER_TIMEOUT_S,
            }
        if self._workspace_server is not None:
            config[_WORKSPACE_MCP_NAME] = {
                "url": self._workspace_server.mcp_url,
                "transport": "streamable-http",
                "timeout": 60,
                "sse_read_timeout": 60,
            }
        return config

    async def _start_ask_user_server(self) -> None:
        """Hosts the ask_user_question MCP tool and registers it with the
        server so the sandboxed agent can actually reach it."""
        try:
            await self._ask_user_server.start()
        except Exception as exc:  # noqa: BLE001 -- the app is still usable without it
            self._on_error(f"Could not start the ask-user service: {exc}")
            return
        try:
            await self._register_ask_user_mcp()
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not register the ask-user tool with OpenHands: {exc}")

    async def _register_ask_user_mcp(self) -> None:
        """Adds this app's MCP endpoint to agent_settings.mcp_config.

        mcp_config is replaced wholesale by the server rather than merged
        per-entry (confirmed live 2026-07-28 -- a partial diff silently wiped
        out the other servers), so the existing dict is read first and resent
        complete. A generous timeout is set because this tool intentionally
        blocks while a human reads the question and clicks.
        """
        settings = await self._client.get_settings()
        mcp_config = dict((settings.get("agent_settings") or {}).get("mcp_config") or {})
        desired = {
            "url": self._ask_user_server.mcp_url,
            "transport": "streamable-http",
            "timeout": _ASK_USER_TIMEOUT_S,
            "sse_read_timeout": _ASK_USER_TIMEOUT_S,
        }
        if mcp_config.get(_ASK_USER_MCP_NAME) == desired:
            return
        mcp_config[_ASK_USER_MCP_NAME] = desired
        await self._client.update_settings({"agent_settings_diff": {"mcp_config": mcp_config}})

    async def _unregister_ask_user_mcp(self) -> None:
        """Removes this app's MCP entry on shutdown.

        Critical, not cosmetic: OpenHands lists tools from every configured
        MCP server before a conversation can start, and a server that does
        not answer takes the whole start down with
        "MCP tool listing timed out after 30 seconds" (reproduced live
        2026-07-29). Leaving the entry behind would therefore break *every*
        new conversation -- including ones started from the browser UI --
        whenever this app is closed.
        """
        try:
            settings = await self._client.get_settings()
            mcp_config = dict((settings.get("agent_settings") or {}).get("mcp_config") or {})
            if mcp_config.pop(_ASK_USER_MCP_NAME, None) is None:
                return
            await self._client.update_settings({"agent_settings_diff": {"mcp_config": mcp_config}})
        except Exception:  # noqa: BLE001 -- best effort during shutdown
            pass

    async def _start_workspace_server(self) -> None:
        """Hosts connect_folder/list_folder/read_file so the agent can reach
        a host folder the server API has no way to mount into its sandbox
        (see core/workspace_server.py). Registered with the agent right away
        (not only after the user picks a folder via the Workspace chip/icon)
        -- the agent needs to see connect_folder exists *before* it needs it,
        so it can call it itself instead of guessing at a sandbox-local path
        the way it did before this tool existed."""
        try:
            await self._workspace_server.start()
        except Exception as exc:  # noqa: BLE001 -- the app is still usable without it
            self._on_error(f"Could not start the workspace folder service: {exc}")
            return
        try:
            await self._register_workspace_mcp()
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not register the workspace folder tool with OpenHands: {exc}")

    async def _register_workspace_mcp(self) -> None:
        settings = await self._client.get_settings()
        mcp_config = dict((settings.get("agent_settings") or {}).get("mcp_config") or {})
        desired = {
            "url": self._workspace_server.mcp_url,
            "transport": "streamable-http",
            "timeout": 60,
            "sse_read_timeout": 60,
        }
        if mcp_config.get(_WORKSPACE_MCP_NAME) == desired:
            return
        mcp_config[_WORKSPACE_MCP_NAME] = desired
        await self._client.update_settings({"agent_settings_diff": {"mcp_config": mcp_config}})

    async def _unregister_workspace_mcp(self) -> None:
        try:
            settings = await self._client.get_settings()
            mcp_config = dict((settings.get("agent_settings") or {}).get("mcp_config") or {})
            if mcp_config.pop(_WORKSPACE_MCP_NAME, None) is None:
                return
            await self._client.update_settings({"agent_settings_diff": {"mcp_config": mcp_config}})
        except Exception:  # noqa: BLE001 -- best effort during shutdown
            pass

    def _on_agent_connected_folder(self, path: str) -> None:
        """Called by WorkspaceFolderServer's connect_folder tool -- the
        agent picked this itself (as opposed to the user via the Workspace
        chip/icon), so this makes it visible instead of a silent state
        change the user has no way to notice."""
        self._append_log(f"Agent connected folder: {path}", kind="system")
        self._workspace_full_text = f"{Path(path).name} (agent-connected)"
        self.workspace_value_label.setText(self._workspace_full_text)
        self.workspace_status_label.setText(f"Workspace: {Path(path).name}")
        self._sync_right_panel()

    async def _confirm_workspace_connect(self, path: str) -> bool:
        """Awaited directly from WorkspaceFolderServer's connect_folder tool
        coroutine (same qasync loop) -- shown non-modally via .open(), same
        reason as _on_agent_question: .exec() would spin a nested Qt loop
        and stall the very MCP request waiting on this answer."""
        if self.mode_combo.currentData() == "bypass":
            self._append_log(
                f"Agent connected your folder without asking (Bypass permissions mode): {path}",
                kind="system",
            )
            return True
        self._append_log(
            f"Agent wants to connect your folder: {path}", kind="system"
        )
        return await self._confirm_dialog(
            "Connect folder?",
            f"The agent wants read access to this folder on your computer:\n\n{path}\n\nAllow?",
        )

    async def _confirm_workspace_write(self, path: str, content: str) -> bool:
        if self.mode_combo.currentData() == "bypass":
            self._append_log(
                f"Agent wrote a file without asking (Bypass permissions mode): {path}",
                kind="system",
            )
            return True
        preview = content if len(content) <= 2000 else content[:2000] + "\n... [truncated]"
        return await self._confirm_dialog(
            "Write file?",
            f"The agent wants to write this file on your computer:\n\n{path}\n\n--- content ---\n{preview}",
        )

    async def _confirm_dialog(self, title: str, text: str) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)

        def _resolve(_result: int) -> None:
            if not future.done():
                future.set_result(box.clickedButton() == box.button(QMessageBox.StandardButton.Yes))

        box.finished.connect(_resolve)
        box.open()  # non-modal: unlike exec(), does not spin a nested event loop
        box.raise_()
        box.activateWindow()
        return await future

    def _on_agent_question(self, pending) -> None:
        """Called on the Qt/asyncio loop by AskUserServer when the agent asks
        something. Every exit path resolves the future -- the agent's tool
        call stays blocked until it does.

        Shown non-modally on purpose: QDialog.exec() spins a nested Qt event
        loop, which would stall qasync's asyncio processing -- and the MCP
        server that is waiting to deliver this very answer runs on that same
        loop. show() keeps it serving while the question is on screen.
        """
        self._append_log(pending.question, kind="agent")
        if self.mode_combo.currentData() != "bypass":
            # Confirmed live 2026-07-31: a real ask_user_question sat
            # unanswered because the app window was minimized/behind others
            # and nobody noticed the non-modal dialog -- the conversation
            # then just sat blocked with no visible sign anything needed
            # attention. Force the whole window forward, not just the
            # dialog, whenever an answer is actually expected from the user.
            self.showNormal()
            self.raise_()
            self.activateWindow()
        if self.mode_combo.currentData() == "bypass":
            # Confirmed live 2026-07-31: an unattended conversation's
            # ask_user_question sat blocked forever with nobody around to
            # answer it, and OpenHands's own stuck detector kept
            # interrupting the *next* thing it tried instead -- the actual
            # blocker (the unanswered question) was never resolved. Bypass
            # mode already means "don't wait for the user" for
            # connect_folder/write_file; extending that here unblocks the
            # agent immediately instead of leaving it stuck on a question
            # nobody will ever see.
            answer = (
                "No user is available to answer right now (Bypass permissions mode). "
                "Use your own best judgment and proceed autonomously -- do not wait "
                "for a response to this question."
            )
            self._append_log(f"[Auto-answered (Bypass permissions mode): {answer}]", kind="system")
            pending.future.set_result(answer)
            return
        dialog = AskUserDialog(self, pending.question, pending.options, pending.multi_select)

        def _resolve() -> None:
            if pending.future.done():
                return
            answer = dialog.answer
            if answer:
                self._append_log(answer, kind="user")
                pending.future.set_result(answer)
            else:
                pending.future.set_result("The user dismissed the question without answering.")

        dialog.finished.connect(lambda _result: _resolve())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    async def _reload_presets_async(self) -> None:
        self._preset_list = await self._presets.list_all()

    def _refresh_sidebar_history(self) -> None:
        asyncio.ensure_future(self._refresh_sidebar_history_async())

    async def _refresh_sidebar_history_async(self) -> None:
        # Confirmed live 2026-07-31: deleting conversations and starting a
        # new one in close succession triggers several of these
        # concurrently (delete's own refresh, plus the new conversation's
        # record_new-then-refresh) -- each does its own independent DB read
        # + sidebar update, and nothing guaranteed they'd finish in the
        # order they started. Whichever finished *last* simply overwrote
        # the sidebar with its own snapshot, which could be one that read
        # the DB before the newer conversation's insert had landed --
        # dropping the brand new entry from view even though it was
        # actually recorded. This sequence guard makes only the
        # most-recently-*requested* refresh ever allowed to actually apply,
        # so a slower, earlier-started one can't clobber a newer result.
        self._sidebar_refresh_seq += 1
        my_seq = self._sidebar_refresh_seq
        records = await self._history.list_recent()
        if my_seq != self._sidebar_refresh_seq:
            return
        self.sidebar.set_records(records)

    # --- event rendering ----------------------------------------------------------

    def _on_error(self, message: str) -> None:
        self._append_log(message, kind="error")

    def _on_events(self, events: list[NormalizedEvent]) -> None:
        for event in events:
            if event.kind in (
                EventKind.ACTION, EventKind.OBSERVATION, EventKind.MESSAGE, EventKind.STREAMING_DELTA,
            ):
                # Timestamped so _on_state_changed can tell a genuinely idle
                # conversation apart from one that's actively producing
                # events but happened to be read as "finished"/"error" by an
                # unluckily-timed status poll -- see the grace-window note
                # there for why a one-shot correction here wasn't enough
                # (confirmed live 2026-07-31: a later poll using
                # still-stale server data flipped the label right back).
                self._last_event_at = datetime.now()
            self._render_event(event)

    def _render_event(self, event: NormalizedEvent) -> None:
        if event.kind == EventKind.STREAMING_DELTA:
            # NOT .strip()'d: each delta is a small raw chunk of the
            # streaming reasoning text, and the leading/trailing space of a
            # chunk is often the actual word-separator between it and its
            # neighbor once concatenated (LogView appends chunks directly
            # in place -- see _append_thinking_as_agent_row). Stripping
            # each chunk individually was silently eating exactly those
            # separators, gluing words together ("Teraz sa" -> "Terazsa")
            # -- confirmed live via a real garbled render.
            reasoning = event.raw.get("reasoning_content") or ""
            if reasoning:
                self._append_log(
                    reasoning, kind="thinking", collapsed=True, title="Thinking",
                    timestamp=event.timestamp,
                )
            return  # normal answer deltas wait for the settled MessageEvent
        if event.kind in (EventKind.ACTION, EventKind.OBSERVATION, EventKind.MESSAGE, EventKind.AGENT_ERROR):
            # The server's own accumulated_token_usage metric is always 0 in
            # this setup (confirmed live across 26 real conversations -- a
            # litellm "model isn't mapped yet" cost-lookup failure appears to
            # short-circuit the whole metrics-recording path upstream, not
            # something fixable from this client). This estimates actual
            # context growth instead, from the full raw event JSON (not the
            # already-truncated log preview) -- a ~4 chars/token heuristic,
            # not exact, but real signal where the server gives none.
            self._estimated_context_chars += len(json.dumps(event.raw, default=str))
            self._update_tool_call_label()
        if event.kind == EventKind.ACTION and self._priority_enforcement_pending:
            self._priority_enforcement_pending = False
            if (
                not self._priority_enforcement_sent
                and event.tool_name != "ask_user_question"
                and self._controller is not None
            ):
                self._priority_enforcement_sent = True
                asyncio.ensure_future(self._send_forced_priority_followup())
        if event.kind == EventKind.ACTION:
            finish_message = None
            if event.tool_name == "finish":
                action = event.raw.get("action") or {}
                finish_message = action.get("message")
            if finish_message:
                self._append_log(finish_message, kind="agent", timestamp=event.timestamp)
                self._flash_if_unfocused()
            else:
                action = event.raw.get("action") or {}
                code_preview = _format_action_code(event.tool_name, action)
                self._append_log(
                    event.tool_name or "?",
                    kind="tool_call",
                    timestamp=event.timestamp,
                    code=code_preview,
                )
                if event.tool_name == "terminal":
                    self._pending_terminal_action_ts = event.timestamp
                if event.tool_name == "task":
                    self._pending_task_action = True
            self._tool_call_count += 1
        elif event.kind == EventKind.OBSERVATION:
            if event.tool_name == "task":
                self._pending_task_action = False
            if event.tool_name == "finish":
                return
            text = (event.text or "").strip()
            observation = event.raw.get("observation") or {}
            meta = None
            if event.tool_name == "terminal":
                # Field names verified live (2026-07-30) against a running
                # agent-server's TerminalObservation JSON -- not guessed.
                # No duration field exists server-side; it's computed here
                # from this observation's own timestamp against the matching
                # action's, both real values off the wire.
                obs_metadata = observation.get("metadata") or {}
                duration_s = None
                if self._pending_terminal_action_ts is not None and event.timestamp is not None:
                    duration_s = (event.timestamp - self._pending_terminal_action_ts).total_seconds()
                self._pending_terminal_action_ts = None
                meta = {
                    "command": observation.get("command"),
                    "working_dir": obs_metadata.get("working_dir"),
                    "exit_code": observation.get("exit_code"),
                    "duration_s": duration_s,
                }
            # A tool call can "succeed" at the protocol level while its own
            # result text reports a real failure -- terminal sets a genuine
            # is_error flag; the workspace folder tools (connect_folder/
            # list_folder/read_file) have no such field and instead return a
            # plain "ERROR: ..." string, the only signal available for them.
            # Either way, this used to render as a green "Success" pill with
            # the actual problem buried inside a collapsed card -- exactly
            # backwards from what the pill is supposed to communicate.
            tool_failed = bool(observation.get("is_error")) or text.startswith("ERROR:")
            if tool_failed:
                self._correct_host_path_confusion_if_needed(event.tool_name, text)
            elif event.tool_name == "terminal" and observation.get("exit_code"):
                # Confirmed live 2026-08-01: a real failed `cd` to a
                # nonexistent (host) path came back with is_error: false --
                # only a non-zero exit_code and a "bash: cd: ... No such
                # file or directory" text revealed it. Checked separately
                # from tool_failed (not folded into it) so an ordinary
                # nonzero exit from e.g. `grep` with no match doesn't start
                # rendering as a red error card -- this only ever feeds the
                # narrowly-gated host-path check, which requires an actual
                # host path prefix and "no such file"/"not a valid
                # directory" text before it does anything.
                self._correct_host_path_confusion_if_needed(event.tool_name, text)
            # Always resolve the matching tool_call card (even with no
            # extracted text) -- LogView pairs tool_call/tool_result into one
            # card and flips its status pill from Running on this call, so
            # skipping empty-text observations would leave that pill stuck
            # on Running forever instead of Success.
            self._append_log(
                text or "(no output)", kind="error" if tool_failed else "tool_result", collapsed=True,
                title=event.tool_name or "Result", timestamp=event.timestamp, meta=meta,
            )
        elif event.kind == EventKind.MESSAGE:
            if event.source == "user" and event.text:
                visible_text = self._visible_user_text(event.text)
                self._remember_user_message(visible_text)
                if self._is_resuming:
                    self._append_log(visible_text, kind="user", timestamp=event.timestamp)
            if event.source == "agent" and event.text:
                # A real text answer right after a priority message means
                # it complied -- clear the pending flag without escalating
                # (only an ACTION event, handled above, triggers the forced
                # follow-up).
                self._priority_enforcement_pending = False
                self._append_log(event.text, kind="agent", timestamp=event.timestamp)
                self._flash_if_unfocused()
        elif event.kind == EventKind.AGENT_ERROR:
            self._append_log(str(event.raw), kind="error", timestamp=event.timestamp)
        elif event.kind == EventKind.CONVERSATION_ERROR:
            # e.g. {"code": "MaxIterationsReached", "detail": "Agent reached
            # maximum iterations limit (50)."} -- verified live 2026-07-30.
            # This is the server calling it quits on the whole conversation,
            # not a single failed tool call, so it's worth a clearer message
            # than a raw dict dump.
            code = event.raw.get("code") or "ConversationError"
            detail = event.raw.get("detail") or "The conversation was stopped by the server."
            self._append_log(f"{code}: {detail}", kind="error", timestamp=event.timestamp)
        elif event.kind == EventKind.CONDENSATION:
            self._append_log("[history condensed]", kind="system")
            self._tool_call_count = 0
            self._estimated_context_chars = 0
            self._real_used_tokens = 0
            self._real_context_window = 0
            self._auto_compact_triggered = False
            self._update_tool_call_label()

    def _append_log(
        self,
        text: str,
        kind: str = "system",
        *,
        collapsed: bool = False,
        title: str | None = None,
        timestamp: datetime | None = None,
        meta: dict | None = None,
        code: str | None = None,
    ) -> None:
        if kind == "error" and "is not a valid directory" not in text.lower():
            # Single choke point: every error in this conversation --
            # controller.error_occurred, AGENT_ERROR events, local
            # validation messages -- ends up as an append_log(kind="error")
            # call somewhere, so tracking it here catches all of them
            # without touching each call site individually. Excludes the
            # "not a valid directory" sandbox-path-probe message (see
            # log_view.append_entry's matching remap) -- not a real error,
            # so it shouldn't inflate the Errors panel count either.
            self._error_history.append((timestamp or datetime.now(), text))
            self._update_errors_button()
        self.log.append_entry(
            kind, text, collapsed=collapsed, title=title, timestamp=timestamp, meta=meta, code=code
        )

    def _on_token_usage_changed(self, used_tokens: int, context_window: int) -> None:
        """Real token usage straight from the server's own metrics.

        These were zero for the whole of 2026-07-28 (a litellm "model isn't
        mapped yet" cost-lookup failure appeared to take the metrics path
        down with it), which is why the char-based estimate below exists.
        The current server populates them properly, so they take over as the
        source of truth as soon as the first LLM call reports them.
        """
        self._real_used_tokens = used_tokens
        self._real_context_window = context_window
        self._update_tool_call_label()

    def _context_usage(self) -> tuple[int, int, bool]:
        """(used_tokens, limit, is_real) for the context-fullness readout.

        Prefers the server's real per-turn token count; falls back to the
        ~4-chars-per-token estimate over raw event JSON only while the
        server hasn't reported anything yet (before the first LLM call, or
        on a server whose metrics are broken).
        """
        if self._real_used_tokens > 0 and self._real_context_window > 0:
            return self._real_used_tokens, self._real_context_window, True
        return self._estimated_context_chars // 4, _CONDENSER_MAX_TOKENS, False

    def _update_tool_call_label(self) -> None:
        used, limit, is_real = self._context_usage()
        prefix = "" if is_real else "~"
        percent = int(used * 100 / limit) if limit else 0
        self.tool_call_label.setText(
            f"{prefix}{used // 1000}K/{limit // 1000}K tokens ({percent}%) · {self._tool_call_count} tool calls"
        )
        self.tool_call_label.setToolTip(
            f"Context: {used} of {limit} tokens"
            + ("" if is_real else " (estimated -- server hasn't reported usage yet)")
        )
        danger = used >= int(limit * 0.9)
        if danger:
            style_name = "ToolCallCountDanger"
        elif used >= int(limit * 0.7):
            style_name = "ToolCallCountWarning"
        else:
            style_name = ""
        self.tool_call_label.setObjectName(style_name)
        _repolish(self.tool_call_label)

        has_conversation = self._controller is not None and self._controller.conversation_id is not None
        self.compact_btn.setEnabled(has_conversation)
        self.compact_btn.setObjectName("CompactButtonActive" if danger and has_conversation else "CompactButton")
        _repolish(self.compact_btn)
        self._sync_right_panel()

        if danger and has_conversation and not self._auto_compact_triggered:
            self._auto_compact_triggered = True
            source = "" if is_real else "estimated "
            self._append_log(
                f"[{source}context {used}/{limit} tokens ({percent}%) -- "
                "automatically requesting history condensation]",
                kind="system",
            )
            self._controller.condense()

    def _compact_now(self) -> None:
        if self._controller is None or self._controller.conversation_id is None:
            return
        self.compact_btn.setEnabled(False)
        self._append_log("[requesting history condensation]", kind="system")
        self._controller.condense()

    def _on_compact_finished(self, success: bool, detail: str) -> None:
        has_conversation = self._controller is not None and self._controller.conversation_id is not None
        self.compact_btn.setEnabled(has_conversation)
        if success:
            self._append_log("[history condensation completed]", kind="system")
        else:
            self._append_log(f"History condensation failed: {detail}", kind="error")

    @staticmethod
    def _visible_user_text(text: str) -> str:
        marker = "\n\nAktualny prikaz:\n"
        if text.startswith("Kontext tejto konverzacie") and marker in text:
            return text.rsplit(marker, 1)[-1]
        return text

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().resizeEvent(event)
        if hasattr(self, "top_bar"):
            self._update_topbar_compact()

    def _update_topbar_compact(self) -> None:
        if not hasattr(self, "top_bar") or not hasattr(self, "model_chip"):
            return
        width = self.top_bar.width()

        # Keep the top bar predictable: primary controls stay visible,
        # secondary actions live in the More menu.
        for widget in (
            self.supervised_agent_btn,
            self.errors_btn,
            self.browser_preview_btn,
            self.changes_btn,
            self.history_btn,
            self.unload_model_btn,
        ):
            widget.setVisible(False)

        self.health_chip.setVisible(True)
        self.workspace_chip.setVisible(True)
        self.thinking_chip.setVisible(True)
        self.keep_chip.setVisible(True)
        self.tasks_btn.setVisible(True)
        self.topbar_status_widget.setVisible(False)
        self.topbar_menu_btn.setVisible(True)
        self.panel_toggle_btn.setVisible(True)

        compact_model = width < 900
        self.model_chip.setMinimumWidth(118 if compact_model else 210)
        self.model_chip.setMaximumWidth(180 if compact_model else 300)
        self.model_combo.setMinimumWidth(54 if compact_model else 112)
        self.model_combo.setMinimumContentsLength(6 if compact_model else 18)
        self.model_caption.setVisible(not compact_model)

        compact_left = width < 900
        self.health_label.setText(self._health_full_text.replace("Health: ", "") if compact_left else self._health_full_text)
        self.workspace_value_label.setText("Workspace" if compact_left else self._workspace_full_text)
        self.health_chip.setMinimumWidth(62 if compact_left else 0)
        self.health_chip.setMaximumWidth(96 if compact_left else 16777215)
        self.workspace_chip.setMinimumWidth(104 if compact_left else 0)
        self.workspace_chip.setMaximumWidth(132 if compact_left else 16777215)
        self.top_bar.updateGeometry()

    def _restore_window_geometry(self) -> None:
        geometry = self._settings.value("window_geometry")
        if isinstance(geometry, bytes):
            self.restoreGeometry(geometry)
        elif hasattr(geometry, "data"):
            self.restoreGeometry(geometry)

    def _restore_splitter_state(self) -> None:
        state = self._settings.value("main_splitter_state")
        restored = False
        if isinstance(state, bytes):
            restored = self.main_splitter.restoreState(state)
        elif hasattr(state, "data"):
            restored = self.main_splitter.restoreState(state)
        if not restored:
            self.main_splitter.setSizes([300, 800, 280])

    def _restore_content_splitter_state(self) -> None:
        state = self._settings.value("content_splitter_state")
        restored = False
        if isinstance(state, bytes):
            restored = self.content_splitter.restoreState(state)
        elif hasattr(state, "data"):
            restored = self.content_splitter.restoreState(state)
        if not restored:
            self.content_splitter.setSizes([560, 150])

    def _save_window_layout(self) -> None:
        self._settings.setValue("window_geometry", self.saveGeometry())
        self._settings.setValue("main_splitter_state", self.main_splitter.saveState())
        self._settings.setValue("content_splitter_state", self.content_splitter.saveState())

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        self._save_window_layout()
        if self._shutdown_complete:
            super().closeEvent(event)
            return
        event.ignore()
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.setEnabled(False)
        asyncio.ensure_future(self._shutdown_before_close())

    async def _shutdown_before_close(self) -> None:
        try:
            if self._controller is not None:
                await self._controller.stop()
            if self in _open_windows:
                _open_windows.remove(self)
            if _open_windows:
                # Other windows are still open and share this process's
                # client/MCP servers -- only the truly last window closing
                # tears those down (see below); this one just closes its
                # own widget.
                return
            closing_conversation_id = (
                self._controller.conversation_id if self._controller is not None else None
            )
            # Unregister BEFORE closing the client -- it needs a live
            # connection, and leaving the entry behind breaks every future
            # conversation (see _unregister_ask_user_mcp).
            await self._unregister_ask_user_mcp()
            await self._ask_user_server.stop()
            await self._unregister_workspace_mcp()
            await self._workspace_server.stop()
            other_conversations_running = False
            try:
                conversations = await self._client.search_conversations(limit=50)
                other_conversations_running = any(
                    c.execution_status is not None
                    and c.execution_status.value == "running"
                    and c.id != closing_conversation_id
                    for c in conversations
                )
            except Exception:  # noqa: BLE001 -- best effort; unload only if we can confirm nothing else needs the model
                other_conversations_running = True
            await self._client.aclose()
            if not other_conversations_running:
                try:
                    process = await asyncio.create_subprocess_exec(
                        "/home/vojtech/.lmstudio/bin/lms",
                        "unload",
                        "--all",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(process.wait(), timeout=30.0)
                except (OSError, asyncio.TimeoutError):
                    pass
        finally:
            self._shutdown_complete = True
            self.close()
