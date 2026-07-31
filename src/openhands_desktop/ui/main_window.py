from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSize, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeyEvent, QMouseEvent, QPainter, QTextCursor
from PySide6.QtWidgets import (
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
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.api.client import AppServerClient
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
from openhands_desktop.ui.log_view import LogView
from openhands_desktop.ui.palette import COLOR_NEUTRAL, TEXT_MUTED
from openhands_desktop.ui.right_panel import RightPanel, panel_toggle_button
from openhands_desktop.ui.settings_dialog import SettingsDialog
from openhands_desktop.ui.sidebar import Sidebar
from openhands_desktop.supervised_agent.openhands_supervisor import ConversationWatchdog
from openhands_desktop.ui.supervised_agent_dialog import SupervisedAgentDialog
from openhands_desktop.ui.spacing import (
    COMPOSER_INPUT_HEIGHT,
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
_TOGGLE_OFF_ICON = QIcon(
    "/home/vojtech/Dokumenty/openhands_settings_svg_kit/components/toggle-off.svg"
)
_TOGGLE_ON_ICON = QIcon(
    "/home/vojtech/Dokumenty/openhands_settings_svg_kit/components/toggle-on.svg"
)

# See MainWindow._on_state_changed: caps automatic interrupt+nudge attempts
# per run so a genuinely broken model loop still surfaces the manual button
# instead of nudging forever. Set to 1 (2026-07-30, user request): give the
# model exactly one automatic redirect, then stop and wait for the user --
# a repeat STUCK after the nudge already tried to fix it means the task
# should halt, not keep retrying the same failing pattern.
_MAX_AUTO_NUDGES_PER_RUN = 1

STATE_LABELS = {
    RunState.IDLE: ("Idle", "StateIdle"),
    RunState.RUNNING: ("Running…", "StateRunning"),
    RunState.PAUSED: ("Paused", "StateWarning"),
    RunState.WAITING_FOR_CONFIRMATION: ("Waiting for confirmation", "StateWarning"),
    # Its own style, not StateWarning: "finished but nobody confirmed it
    # actually succeeded" must never look like the same thing as "paused" or
    # blend into "completed" -- see the design spec's explicit call-out that
    # Completed and Finished-unverified must stay unambiguous at a glance.
    RunState.FINISHED_UNVERIFIED: ("Finished (unverified)", "StateFinishedUnverified"),
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
    """Multi-line input: Ctrl+Enter sends, Escape requests a stop, plain
    Enter inserts a newline (so multi-line tasks are still easy to type)."""

    def __init__(self, on_send, on_stop, on_focus_change=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_send = on_send
        self._on_stop = on_stop
        self._on_focus_change = on_focus_change
        self.setPlaceholderText("What would you like the agent to do? (Ctrl+Enter to send)")
        self.setFixedHeight(72)

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
            self._on_stop()
            return
        super().keyPressEvent(event)


# Windows opened via "Continue as Code" (2026-07-31) need a reference kept
# somewhere for the lifetime of the window -- nothing else in the app holds
# one, and a QWidget with no Python (or Qt-parent) reference left is fair
# game for Python's GC despite still being shown on screen.
_secondary_windows: list["MainWindow"] = []


class MainWindow(QMainWindow):
    def __init__(
        self,
        client: AppServerClient,
        history_store: HistoryStore | None = None,
        *,
        shared_ask_user_server: AskUserServer | None = None,
        shared_workspace_server: WorkspaceFolderServer | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("OpenHands Desktop")
        self.resize(1100, 720)
        self.setStyleSheet(DARK_QSS)

        self._client = client
        self._settings = QSettings("OpenHandsDesktop", "MainWindow")
        self._ensure_default_model_settings()
        self._profiles_by_name: dict[str, object] = {}
        self._history = history_store or HistoryStore()
        self._presets = PresetStore()
        self._preset_list: list[Preset] = []
        self._pending_model: str | None = None
        self._pending_title: str | None = None
        self._current_agent_type: str | None = None
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
        self._recent_user_messages: list[str] = []
        self._is_resuming = False
        self._conversation_starting = False
        self._model_switching = False
        self._shutdown_complete = False
        self._shutdown_started = False
        self._controller: ConversationController | None = None
        self._settings_dialog: SettingsDialog | None = None
        # A second window ("Continue as Code" opening its own window,
        # 2026-07-31) can't start its own AskUserServer/WorkspaceFolderServer
        # -- they're fixed-port (8901/8902) singletons for this whole
        # process, and a second uvicorn.Server on the same port would just
        # fail to bind. It reuses the first window's already-running
        # instances instead; confirmation dialogs for its conversations
        # then show on the *first* window (whichever one owns the callback
        # these were constructed with), not perfectly attributed but never
        # silently dropped either. _owns_mcp_servers gates start/stop and
        # registration so only the owning window does any of that.
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

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.icon_rail = IconRail()
        self.icon_rail.workspace_requested.connect(self._check_workspace)
        self.icon_rail.settings_requested.connect(self._open_settings)
        root_layout.addWidget(self.icon_rail)

        self.sidebar = Sidebar()
        self.sidebar.new_conversation_requested.connect(self._start_fresh_conversation)
        self.sidebar.conversation_selected.connect(self._resume_conversation)
        self.sidebar.conversation_delete_requested.connect(self._confirm_delete_conversation)
        self.sidebar.settings_requested.connect(self._open_settings)
        root_layout.addWidget(self.sidebar)

        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        root_layout.addWidget(main_area, 1)

        self.right_panel = RightPanel()
        self.right_panel.setVisible(False)
        root_layout.addWidget(self.right_panel)

        # --- top bar ---
        top_bar = QWidget()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(TOOLBAR_HEIGHT)
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        top_bar_layout.setSpacing(SPACE_SM)

        # Menu + logo live in the sidebar itself (top-left of the whole
        # window, per the target mockup) -- not repeated here.

        # Health chip
        self.health_icon_label = QLabel()
        self.health_icon_label.setPixmap(icon("health", 14, COLOR_NEUTRAL).pixmap(14, 14))
        self.health_label = QLabel("Health: unknown")
        top_bar_layout.addWidget(
            self._simple_chip([self.health_icon_label, self.health_label])
        )

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
        self.workspace_value_label = QLabel("Check permissions…")
        self._fill_two_line_chip(
            self.workspace_chip, "workspace", "Workspace", self.workspace_value_label
        )
        self.workspace_chip.clicked.connect(self._check_workspace)
        top_bar_layout.addWidget(self.workspace_chip)

        # Model chip: caption + a real QComboBox styled borderless as the
        # "value" line (this one IS a functional selector, unlike Workspace).
        model_chip = QWidget()
        model_chip.setObjectName("TopBarChip")
        model_chip.setFixedHeight(TOOLBAR_CONTROL_HEIGHT)
        model_chip_row = QHBoxLayout(model_chip)
        model_chip_row.setContentsMargins(SPACE_SM, 4, SPACE_XS, 4)
        model_chip_row.setSpacing(SPACE_XS)
        model_icon_label = QLabel()
        model_icon_label.setPixmap(icon("model-ai", 18).pixmap(18, 18))
        model_chip_row.addWidget(model_icon_label)
        model_text_col = QVBoxLayout()
        model_text_col.setSpacing(0)
        model_caption = QLabel("Model")
        model_caption.setObjectName("ChipCaption")
        model_text_col.addWidget(model_caption)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ChipValue")
        self.model_combo.setFrame(False)
        self.model_combo.setStyleSheet("QComboBox#ChipValue { background: transparent; border: none; padding: 0px; }")
        self.model_combo.setToolTip("LLM profile used for the next new conversation")
        self.model_combo.setItemDelegate(_CurrentSelectionDelegate(self.model_combo))
        self.model_combo.currentIndexChanged.connect(self._on_model_selection_changed)
        model_text_col.addWidget(self.model_combo)
        model_chip_row.addLayout(model_text_col, 1)
        top_bar_layout.addWidget(model_chip)

        thinking_chip = QWidget()
        thinking_chip_layout = QVBoxLayout(thinking_chip)
        thinking_chip_layout.setContentsMargins(0, 0, 0, 0)
        thinking_chip_layout.setSpacing(1)
        thinking_label = QLabel("Think")
        thinking_label.setObjectName("ChipCaption")
        thinking_chip_layout.addWidget(thinking_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.enable_thinking_check = QPushButton()
        self.enable_thinking_check.setCheckable(True)
        self.enable_thinking_check.setFixedSize(38, 22)
        self.enable_thinking_check.setIconSize(QSize(38, 22))
        self.enable_thinking_check.setObjectName("ComposerToolButton")
        self.enable_thinking_check.setToolTip(
            "Thinking: enable model reasoning for the selected LLM profile. "
            "Applies to the next new conversation."
        )
        self.enable_thinking_check.toggled.connect(self._on_thinking_toggle_changed)
        thinking_chip_layout.addWidget(self.enable_thinking_check)
        top_bar_layout.addWidget(thinking_chip)

        keep_chip = QWidget()
        keep_chip_layout = QVBoxLayout(keep_chip)
        keep_chip_layout.setContentsMargins(0, 0, 0, 0)
        keep_chip_layout.setSpacing(1)
        keep_label = QLabel("Keep")
        keep_label.setObjectName("ChipCaption")
        keep_chip_layout.addWidget(keep_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.preserve_thinking_check = QPushButton()
        self.preserve_thinking_check.setCheckable(True)
        self.preserve_thinking_check.setFixedSize(38, 22)
        self.preserve_thinking_check.setIconSize(QSize(38, 22))
        self.preserve_thinking_check.setObjectName("ComposerToolButton")
        self.preserve_thinking_check.setToolTip(
            "Keep thinking: preserve previous thinking blocks across turns for the selected LLM profile. "
            "Applies to the next new conversation."
        )
        self.preserve_thinking_check.toggled.connect(self._on_thinking_toggle_changed)
        keep_chip_layout.addWidget(self.preserve_thinking_check)
        top_bar_layout.addWidget(keep_chip)
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
        self.browser_preview_btn.setToolTip(
            "Open a live view of the sandbox's browser (noVNC) in your system browser -- "
            "shows what browser_navigate/browser_get_state are actually doing"
        )
        self.browser_preview_btn.setEnabled(False)
        self.browser_preview_btn.clicked.connect(self._open_browser_preview)
        top_bar_layout.addWidget(self.browser_preview_btn)

        self.errors_btn = QPushButton("Errors")
        self.errors_btn.setToolTip("Every error in this conversation, collected in one place")
        self.errors_btn.setEnabled(False)
        self.errors_btn.clicked.connect(self._open_errors_dialog)
        top_bar_layout.addWidget(self.errors_btn)

        self.tasks_btn = QPushButton("Tasks")
        self.tasks_btn.setIcon(icon("plan-tasks"))
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

        status_col = QVBoxLayout()
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
        top_bar_layout.addLayout(status_col)

        self.panel_toggle_btn = panel_toggle_button()
        self.panel_toggle_btn.toggled.connect(self.right_panel.setVisible)
        top_bar_layout.addWidget(self.panel_toggle_btn)

        main_layout.addWidget(top_bar)

        # --- stacked content: welcome vs. live chat log ---
        self.stack = QStackedWidget()
        self.welcome = WelcomeWidget()
        self.welcome.suggestion_clicked.connect(self._on_suggestion_clicked)
        self.log = LogView()
        self.log.retry_requested.connect(self._retry_last_message)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.log)
        self.stack.setCurrentWidget(self.welcome)
        main_layout.addWidget(self.stack, 1)

        # --- composer ---
        # Single bar, per the message-composer.svg reference (2026-07-30):
        # attach icon inline with the input on top, agent-type pill + tool
        # buttons + Send/Stop all in one row underneath -- replacing the
        # previous two-column layout (composer frame + a separate Send/Stop
        # button stack beside it).
        input_row = QWidget()
        input_row_layout = QVBoxLayout(input_row)
        input_row_layout.setContentsMargins(SPACE_MD, SPACE_XS, SPACE_MD, SPACE_SM)
        input_row_layout.setSpacing(0)

        self.composer_frame = QFrame()
        composer_frame = self.composer_frame
        composer_frame.setObjectName("ComposerFrame")
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
            on_send=self._send, on_stop=self._interrupt, on_focus_change=self._on_composer_focus_change
        )
        self.input.setObjectName("ComposerInput")
        self.input.setFixedHeight(COMPOSER_INPUT_HEIGHT - 40)
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

        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.setIcon(icon("stop", 15))
        self.stop_btn.setFixedSize(32, 28)
        self.stop_btn.setToolTip("Immediately interrupt the agent's in-flight LLM call (Esc)")
        self.stop_btn.clicked.connect(self._interrupt)
        composer_tools_row.addWidget(self.stop_btn)

        composer_layout.addLayout(composer_tools_row)
        input_row_layout.addWidget(composer_frame)

        main_layout.addWidget(input_row)

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
                self._on_error("No sandbox for this conversation yet -- try again once it's running.")
                return
            url = await self._client.get_novnc_url(conversation.sandbox_id)
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Could not open browser preview: {exc}")
            return
        if not url:
            self._on_error(
                "No noVNC preview available for this sandbox (image may have been "
                "started without OH_ENABLE_VNC=1)."
            )
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
        dialog.exec()

    def _save_to_mempalace(self) -> None:
        if self._controller is None or self._controller.conversation_id is None:
            return
        instruction = (
            "Please call mempalace_checkpoint now to save a diary entry (and any relevant "
            "drawer items) summarizing this session, in AAAK format."
        )
        self._append_log(instruction, kind="user")
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
        try:
            await self._client.delete_conversation(conversation_id)
        except Exception:  # noqa: BLE001 -- server-side record may already be gone; still drop it locally
            pass
        await self._history.delete(conversation_id)
        if self._controller is not None and self._controller.conversation_id == conversation_id:
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
            profiles, active_profile = await self._client.list_llm_profiles()
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
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

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
        for p in profiles:
            self.model_combo.addItem(icon("model-ai"), f"{p.name} ({p.model})", userData=p.name)
        remembered = self._settings.value("last_profile_name")
        target = remembered if remembered in self._profiles_by_name else active_profile
        if target is not None:
            index = self.model_combo.findData(target)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._update_model_status_label()
        self._refresh_thinking_toggles()
        self._check_loaded_model(auto_select=True)

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
            self.enable_thinking_check.setEnabled(False)
            self.preserve_thinking_check.setEnabled(False)
            return
        self.enable_thinking_check.setEnabled(False)
        self.preserve_thinking_check.setEnabled(False)
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
        self.enable_thinking_check.setEnabled(False)
        self.preserve_thinking_check.setEnabled(False)
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
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Failed to save thinking settings for {profile_name}: {exc}")
        finally:
            if profile_name == self.model_combo.currentData():
                self.enable_thinking_check.setEnabled(True)
                self.preserve_thinking_check.setEnabled(True)

    def _update_thinking_toggle_icons(self) -> None:
        self.enable_thinking_check.setIcon(
            _TOGGLE_ON_ICON if self.enable_thinking_check.isChecked() else _TOGGLE_OFF_ICON
        )
        self.preserve_thinking_check.setIcon(
            _TOGGLE_ON_ICON if self.preserve_thinking_check.isChecked() else _TOGGLE_OFF_ICON
        )

    def _update_model_status_label(self) -> None:
        model = self._selected_model()
        self.model_status_label.setText(f"Model: {model}" if model else "")
        self.model_status_label.setObjectName("")
        self.model_status_label.setToolTip("")
        _repolish(self.model_status_label)

    def _check_loaded_model(self, *, auto_select: bool = False) -> None:
        asyncio.ensure_future(self._check_loaded_model_async(auto_select=auto_select))

    async def _check_loaded_model_async(self, *, auto_select: bool = False) -> None:
        if not self._profiles_by_name:
            return
        detected = await detect_loaded_model()
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

    def _selected_model(self) -> str | None:
        name = self.model_combo.currentData()
        profile = self._profiles_by_name.get(name) if name else None
        return profile.model if profile else None

    # --- health / workspace ----------------------------------------------------

    def _check_health(self) -> None:
        asyncio.ensure_future(self._check_health_async())

    async def _check_health_async(self) -> None:
        ok = await self._client.health()
        self.health_label.setText(f"Health: {'OK' if ok else 'unreachable'}")
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
        self._llm_server_state = await probe_llm_server_state() if ok else "unreachable"
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
        self.workspace_value_label.setText(
            f"{Path(path).name} ({'OK' if result.ok else 'issues found'})"
        )
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
            # Verified live 2026-07-30: a message referencing a host path
            # (e.g. "/home/user/project") sent the agent looking for it
            # inside its own sandbox, where it doesn't exist -- the
            # app-server API has no per-conversation way to mount an
            # arbitrary host directory (see client.py's DEFAULT_WORKSPACE_PATH
            # note), so this can never actually resolve. Left the agent to
            # guess (it cloned the wrong GitHub repo trying to "find" it),
            # which then blew away its own sandbox root. Suppressed once a
            # folder is actually connected via the Workspace chip/icon
            # (workspace_server.py) -- that's the real fix; this note is
            # just insurance against repeating the mistake blind.
            self._append_log(
                f"Heads up: \"{host_path}\" looks like a path on your computer. "
                "The agent's sandbox (/workspace/project) can't see it directly -- "
                "connect a folder via the Workspace button/icon first so the agent "
                "can actually read it (list_folder/read_file tools).",
                kind="system",
            )
        self._auto_nudge_count_for_run = 0
        if self._controller.conversation_id is None:
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
            self._controller.send_message(text)

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
        controller.start_new(
            llm_model=model,
            initial_message=text,
            agent_type=agent_type,
            system_message_suffix=self._custom_instructions(),
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

    def _custom_instructions(self) -> str | None:
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
        return self._custom_instructions_text or None

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

    def _interrupt(self) -> None:
        if self._controller is not None:
            self._controller.interrupt()

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
        asyncio.ensure_future(self._record_and_refresh(conversation_id))

    async def _record_and_refresh(self, conversation_id: str) -> None:
        await self._history.record_new(
            conversation_id, llm_model=self._pending_model, title=self._pending_title
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

    def _on_state_changed(self, state: RunState) -> None:
        if state in self._TERMINAL_STATES:
            # Freeze instead of continuing to tick through idle time after
            # the agent is actually done -- "how long this ran", not "how
            # long since it started including time spent waiting".
            self._update_duration_label()
            self._duration_frozen = True
        elif self._duration_frozen and state == RunState.RUNNING:
            self._duration_frozen = False
        label, style_name = STATE_LABELS.get(state, (str(state), ""))
        self.state_label.setText(label)
        self.state_label.setObjectName(style_name)
        self.state_label.setToolTip(
            "Agent finished without an explicit finish confirmation from the model."
            if state == RunState.FINISHED_UNVERIFIED
            else ""
        )
        _repolish(self.state_label)
        if self._controller is not None and self._controller.conversation_id is not None:
            asyncio.ensure_future(
                self._history.update_status(self._controller.conversation_id, state.name)
            )
        # Plan -> Act handoff (borrowed from Claude Code's plan mode / Roo
        # Code's Orchestrator): once a Plan conversation reaches a terminal
        # state, offer to hand its output to a fresh Code conversation
        # instead of the user copying it over by hand.
        plan_finished = self._current_agent_type == "plan" and state in (
            RunState.COMPLETED,
            RunState.FINISHED_UNVERIFIED,
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
            else:
                # Out of auto-attempts (or one is already in flight) --
                # leave it to the user.
                self.stuck_nudge_btn.setVisible(not self._auto_nudge_in_flight)
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
            self._append_log(f"[Auto-supervise: stopped -- {event.get('reason')}]", kind="system")

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
            self._append_log(self._NUDGE_TEXT, kind="user")
            self._controller.send_message(self._NUDGE_TEXT)
        finally:
            self._auto_nudge_in_flight = False

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
        # 2026-07-31: opens in a brand new window instead of reusing/
        # mutating this one's widgets. Reusing this window's state_label
        # required an explicit reset-to-"Starting…" (still done for the
        # window that opens itself, see _start_as_code_continuation) and was
        # still fragile -- e.g. Mission Control or the sidebar reading this
        # window's mid-transition state. A second window can't show a stale
        # value from a conversation it never had; this one just keeps
        # showing the Plan conversation's real, correct "Finished" state.
        if self._controller is None or self._controller.conversation_id is None:
            return
        parent_id = self._controller.conversation_id
        self.continue_as_code_btn.setVisible(False)
        default_code_model = self._settings.value("default_code_model_name")
        new_window = MainWindow(
            AppServerClient(self._client.base_url),
            shared_ask_user_server=self._ask_user_server,
            shared_workspace_server=self._workspace_server,
        )
        new_window.setWindowIcon(self.windowIcon())
        _secondary_windows.append(new_window)
        new_window.show()
        asyncio.ensure_future(new_window._start_as_code_continuation(parent_id, default_code_model))

    async def _start_as_code_continuation(self, parent_id: str, profile_name: str | None) -> None:
        """Runs on the freshly opened window from _continue_as_code_async,
        right after construction -- does the same model-load + start_new
        _continue_as_code_async used to do in place, minus every step that
        was only there to reset *this* window's widgets away from a stale
        previous conversation (a brand new window has nothing stale to
        reset)."""
        await self._load_custom_instructions()
        if profile_name:
            self.model_combo.blockSignals(True)
            self._switch_to_named_model(profile_name)
            self.model_combo.blockSignals(False)
            self._set_model_switching(True)
            try:
                await self._ensure_profile_ready(profile_name)
            except Exception as exc:  # noqa: BLE001
                self._on_error(f"Could not load the Code model: {exc}")
                return
            finally:
                self._set_model_switching(False)
        self._current_agent_type = "default"
        self.agent_type_combo.setCurrentIndex(self.agent_type_combo.findData("default"))
        self.agent_type_combo.setEnabled(False)
        model = self._selected_model()
        self._pending_model = model
        self._pending_title = "Continued from plan"
        self.stack.setCurrentWidget(self.log)
        self._append_log(f"[continuing plan from conversation {parent_id} as a Code agent…]")
        self._controller.start_new(
            llm_model=model,
            initial_message="Continue implementing the plan from the previous conversation.",
            agent_type="default",
            parent_conversation_id=parent_id,
            system_message_suffix=self._custom_instructions(),
        )

    # --- history / sidebar -------------------------------------------------------

    async def _init_history_and_refresh(self) -> None:
        await self._history.init()
        await self._refresh_sidebar_history_async()
        await self._presets.init()
        await self._reload_presets_async()
        if self._owns_mcp_servers:
            await self._start_ask_user_server()
            await self._start_workspace_server()
        await self._load_custom_instructions()

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
        self.workspace_value_label.setText(f"{Path(path).name} (agent-connected)")
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
        records = await self._history.list_recent()
        self.sidebar.set_records(records)

    # --- event rendering ----------------------------------------------------------

    def _on_error(self, message: str) -> None:
        self._append_log(message, kind="error")

    def _on_events(self, events: list[NormalizedEvent]) -> None:
        for event in events:
            self._render_event(event)

    def _render_event(self, event: NormalizedEvent) -> None:
        if event.kind == EventKind.STREAMING_DELTA:
            reasoning = (event.raw.get("reasoning_content") or "").strip()
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
        if event.kind == EventKind.ACTION:
            finish_message = None
            if event.tool_name == "finish":
                action = event.raw.get("action") or {}
                finish_message = action.get("message")
            if finish_message:
                self._append_log(finish_message, kind="agent", timestamp=event.timestamp)
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
            self._tool_call_count += 1
        elif event.kind == EventKind.OBSERVATION:
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
                self._append_log(event.text, kind="agent", timestamp=event.timestamp)
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

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
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
            closing_conversation_id = (
                self._controller.conversation_id if self._controller is not None else None
            )
            if self._controller is not None:
                await self._controller.stop()
            if self._owns_mcp_servers and not _secondary_windows:
                # Unregister BEFORE closing the client -- it needs a live
                # connection, and leaving the entry behind breaks every
                # future conversation (see _unregister_ask_user_mcp). A
                # non-owning window must never do this -- it would rip the
                # tool out from under whichever window actually owns it. If
                # any secondary window is still open, it's depending on
                # these same shared instances -- leave them running rather
                # than break it out from under it; nothing currently stops
                # them in that case; a real cleanup path can be added if
                # this order (primary closes first, children stay open)
                # turns out to be common in practice.
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
            if self in _secondary_windows:
                _secondary_windows.remove(self)
            self.close()
