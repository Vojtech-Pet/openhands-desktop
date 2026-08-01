"""Settings dialog. Left nav with the 9 sections from the real OpenHands
settings menu (per the provided menu-theme SVG kit's manifest.json
`menu_order`). Every section is wired to the real OpenHands agent-server API
(confirmed live against a running server on 2026-07-28 -- see client.py for
the endpoints), not a mockup:

- LLM: full CRUD (add/edit/delete/activate) via /api/v1/settings/profiles.
- Agent, Condenser, Verification, Application: GET/POST /api/v1/settings
  (agent_settings_diff / conversation_settings_diff / top-level fields).
- MCP: agent_settings.mcp_config -- special-cased because the server
  replaces this field wholesale rather than merging per-entry (confirmed
  live), so add/remove always resend the complete server dict.
- Skills: /api/v1/skills/search (list) + agent_context.disabled_skills
  (toggle), instant per-row save.
- Integrations: /api/v1/secrets/git-providers (the server validates tokens
  against the real provider API before storing them).
- Secrets: full CRUD via /api/v1/secrets.

Only two things aren't backed by a real endpoint: the Application page's
Theme control (no light theme exists in this app yet) and the Slack field
some earlier draft had (no such endpoint exists at all) -- both are simply
not shown rather than faked.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.api.llm_server_client import LoadedModel, detect_loaded_model
from openhands_desktop.api.models import LlmProfile
from openhands_desktop.ui.async_utils import run_async as _run_async
from openhands_desktop.ui.icons import icon, menu_icon
from openhands_desktop.ui.palette import (
    BG_SURFACE_1,
    BG_SURFACE_2,
    BORDER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from openhands_desktop.ui.spacing import RADIUS_LG, RADIUS_MD, SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XS

_SECTIONS = ["Agent", "LLM", "Condenser", "Verification", "MCP", "Skills", "Integrations", "Application", "Secrets"]
_ICON_FOR = {
    "Agent": "agent",
    "LLM": "llm",
    "Condenser": "condenser",
    "Verification": "verification",
    "MCP": "mcp",
    "Skills": "skills",
    "Integrations": "integrations",
    "Application": "application",
    "Secrets": "secrets",
}
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_INVALID_PROFILE_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_LANGUAGE_CODES = [("English", "en"), ("Slovenčina", "sk"), ("Čeština", "cs")]


def _saved_flash(button: QPushButton, text: str = "Saved") -> None:
    original = button.text()
    button.setText(text)
    button.setEnabled(False)
    QTimer.singleShot(1200, lambda: (button.setText(original), button.setEnabled(True)))


def _page(title: str, icon_name: str, description: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
    layout.setSpacing(SPACE_MD)

    header_row = QHBoxLayout()
    header_row.setSpacing(SPACE_SM)
    icon_label = QLabel()
    icon_label.setPixmap(menu_icon(icon_name, 22, TEXT_SECONDARY).pixmap(22, 22))
    header_row.addWidget(icon_label)
    title_label = QLabel(title)
    title_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: 600;")
    header_row.addWidget(title_label)
    header_row.addStretch()
    layout.addLayout(header_row)

    desc_label = QLabel(description)
    desc_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 13px;")
    desc_label.setWordWrap(True)
    layout.addWidget(desc_label)

    return page, layout


def _form(layout: QVBoxLayout) -> QFormLayout:
    form = QFormLayout()
    form.setSpacing(SPACE_SM)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    layout.addLayout(form)
    return form


def _row_widget(color: str = BORDER) -> tuple[QWidget, QHBoxLayout]:
    row = QWidget()
    row.setObjectName("SettingsRow")
    row.setStyleSheet(
        f"#SettingsRow {{ background-color: {BG_SURFACE_2}; border: 1px solid {color}; "
        f"border-radius: {RADIUS_MD}px; }}"
    )
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
    return row, row_layout


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------


# Recommended sampling settings for the two Qwen model shapes this app is
# actually configured against (confirmed against Qwen's own published specs,
# 2026-07-28 -- see Hugging Face model cards for Qwen3.6-35B-A3B / the 27B
# dense variant). "Precise coding" uses thinking mode at a lower temperature,
# which is what SWE-bench-style agentic coding is tuned/benchmarked with;
# "general" is the higher-temperature thinking preset for open-ended tasks.
# min_p / presence_penalty / repeat_penalty / preserve_thinking aren't
# top-level StrictLLM fields -- they only exist inside litellm_extra_body
# (confirmed live via GET /api/v1/settings/profiles/{name}).
#
# NOTE on the field name (2026-07-30): llama.cpp's server -- what LM Studio
# actually runs under the hood -- reads "repeat_penalty", not
# "repetition_penalty" (the litellm/vLLM name). This code used to send the
# latter, so the field was silently dropped as an unrecognized key and never
# had any effect. dry_multiplier/dry_base/dry_allowed_length (llama.cpp's
# DRY sampler, off by default at multiplier=0.0) are added below for the
# same reason: DRY catches *sequence*-level repeats (a model re-treading the
# same reasoning/search pattern in different words), which is exactly the
# STUCK failure mode -- plain repeat_penalty only catches identical tokens
# recurring, not paraphrased loops.
_LLM_PRESETS = [
    (
        "Qwen A3B -- precise coding (thinking)",
        {
            "temperature": 0.6, "top_p": 0.95, "top_k": 20,
            "max_input_tokens": 24000, "max_output_tokens": 4096,
            "min_p": 0.0, "presence_penalty": 0.0, "repeat_penalty": 1.0,
            "enable_thinking": True, "preserve_thinking": False,
        },
    ),
    (
        "Qwen A3B -- general (thinking)",
        {
            "temperature": 1.0, "top_p": 0.95, "top_k": 20,
            "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0,
            "max_input_tokens": 24000, "max_output_tokens": 4096,
            "enable_thinking": True, "preserve_thinking": False,
        },
    ),
    (
        "Qwen 27B -- precise coding (thinking)",
        {
            "temperature": 0.6, "top_p": 0.95, "top_k": 20,
            "max_input_tokens": 24000, "max_output_tokens": 4096,
            "min_p": 0.0, "presence_penalty": 0.0, "repeat_penalty": 1.0,
            "enable_thinking": True, "preserve_thinking": False,
        },
    ),
    (
        "Qwen 27B -- general / non-thinking",
        {
            "temperature": 0.7, "top_p": 0.80, "top_k": 20,
            "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0,
            "max_input_tokens": 24000, "max_output_tokens": 4096,
            "enable_thinking": False, "preserve_thinking": False,
        },
    ),
]


class _ProfileFormDialog(QDialog):
    """Add/Edit form for one LLM profile. Same shape covers both: Add has an
    editable, empty Name field; Edit pre-fills every field (including the
    real sampling settings, fetched via get_profile_detail since the list
    endpoint doesn't carry them) and disables Name. A "Recommended preset"
    picker pre-fills the sampling fields from Qwen's own published specs --
    still fully editable afterward, and Model/Base URL/API key are never
    touched by picking one. Maps directly onto AppServerClient.save_profile's
    fields."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        profile: LlmProfile | None = None,
        detail: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing = profile is not None
        self.setWindowTitle("Edit profile" if self._editing else "Add profile")
        detail = detail or {}
        self._detail = dict(detail)
        extra_body = detail.get("litellm_extra_body") or {}
        chat_template_kwargs = extra_body.get("chat_template_kwargs") or {}
        enable_thinking = chat_template_kwargs.get("enable_thinking", False)
        preserve_thinking = chat_template_kwargs.get("preserve_thinking", False)

        layout = QVBoxLayout(self)

        self._preset_combo = QComboBox()
        self._preset_combo.addItem("Recommended preset…")
        for label, _values in _LLM_PRESETS:
            self._preset_combo.addItem(label)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        layout.addWidget(self._preset_combo)

        form = QFormLayout()
        form.setSpacing(SPACE_SM)
        layout.addLayout(form)

        self._name_field = QLineEdit(profile.name if profile else "")
        self._name_field.setEnabled(not self._editing)
        self._name_field.setPlaceholderText("e.g. my-profile")
        form.addRow("Name", self._name_field)

        model_row = QHBoxLayout()
        self._model_field = QLineEdit(profile.model if profile else "")
        self._model_field.setPlaceholderText("e.g. openai/gpt-5.5")
        model_row.addWidget(self._model_field, 1)
        autodetect_btn = QPushButton("Autodetect")
        autodetect_btn.setToolTip(
            "Find the running LM Studio server and the model loaded right now "
            "(uses LM Studio's /api/v0/models)"
        )
        autodetect_btn.clicked.connect(self._on_autodetect_clicked)
        model_row.addWidget(autodetect_btn)
        form.addRow("Model", model_row)

        self._base_url_field = QLineEdit((profile.base_url or "") if profile else "")
        self._base_url_field.setPlaceholderText("optional, e.g. http://127.0.0.1:1234/v1")
        form.addRow("Base URL", self._base_url_field)

        self._api_key_field = QLineEdit()
        self._api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_field.setPlaceholderText(
            "leave blank to keep existing key" if profile and profile.api_key_set else "API key"
        )
        form.addRow("API key", self._api_key_field)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.05)
        self._temperature.setValue(detail.get("temperature", 0.7))
        form.addRow("Temperature", self._temperature)

        self._top_p = QDoubleSpinBox()
        self._top_p.setRange(0.0, 1.0)
        self._top_p.setSingleStep(0.01)
        self._top_p.setValue(detail.get("top_p", 0.8))
        form.addRow("Top P", self._top_p)

        self._top_k = QSpinBox()
        self._top_k.setRange(0, 200)
        self._top_k.setValue(int(detail.get("top_k", 20)))
        form.addRow("Top K", self._top_k)

        self._min_p = QDoubleSpinBox()
        self._min_p.setRange(0.0, 1.0)
        self._min_p.setSingleStep(0.01)
        self._min_p.setValue(extra_body.get("min_p", 0.0))
        form.addRow("Min P", self._min_p)

        self._presence_penalty = QDoubleSpinBox()
        self._presence_penalty.setRange(0.0, 2.0)
        self._presence_penalty.setSingleStep(0.1)
        self._presence_penalty.setValue(extra_body.get("presence_penalty", 0.0))
        form.addRow("Presence penalty", self._presence_penalty)

        self._enable_thinking = QCheckBox("Enable thinking")
        self._enable_thinking.setChecked(bool(enable_thinking))
        form.addRow("", self._enable_thinking)

        self._preserve_thinking = QCheckBox("Preserve thinking across turns")
        self._preserve_thinking.setChecked(bool(preserve_thinking))
        form.addRow("", self._preserve_thinking)

        self._pending_token_limits = {
            "max_input_tokens": detail.get("max_input_tokens", 24000),
            "max_output_tokens": detail.get("max_output_tokens", 4096),
        }

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_preset_selected(self, index: int) -> None:
        if index == 0:
            return
        _label, values = _LLM_PRESETS[index - 1]
        self._temperature.setValue(values.get("temperature", self._temperature.value()))
        self._top_p.setValue(values.get("top_p", self._top_p.value()))
        self._top_k.setValue(values.get("top_k", self._top_k.value()))
        self._min_p.setValue(values.get("min_p", self._min_p.value()))
        self._presence_penalty.setValue(values.get("presence_penalty", self._presence_penalty.value()))
        self._enable_thinking.setChecked(bool(values.get("enable_thinking", values.get("preserve_thinking", False))))
        self._preserve_thinking.setChecked(bool(values.get("preserve_thinking")))
        if "max_output_tokens" in values or "max_input_tokens" in values:
            self._pending_token_limits = values

    def _on_autodetect_clicked(self) -> None:
        base_url = self._base_url_field.text().strip()
        _run_async(
            self,
            detect_loaded_model((base_url,)) if base_url else detect_loaded_model(),
            self._on_autodetect_result,
            error_title="Autodetect failed",
        )

    def _on_autodetect_result(self, detected: LoadedModel | None) -> None:
        if not detected:
            QMessageBox.information(
                self,
                "No model detected",
                "Couldn't detect a running LM Studio server with a loaded model. "
                "Start the server, load a model, or enter Base URL manually.",
            )
            return
        model_id = detected.model_id
        self._base_url_field.setText(detected.base_url)
        self._model_field.setText(f"openai/{model_id}")
        if not self._name_field.text().strip() and self._name_field.isEnabled():
            suggested = model_id.rsplit("/", 1)[-1][:64]
            self._name_field.setText(_INVALID_PROFILE_NAME_CHARS_RE.sub("-", suggested))

    def _on_accept(self) -> None:
        name = self._name_field.text().strip()
        model = self._model_field.text().strip()
        if not name or not model:
            QMessageBox.warning(self, "Missing fields", "Name and Model are required.")
            return
        if not _PROFILE_NAME_RE.match(name):
            QMessageBox.warning(
                self, "Invalid name", "Name may only contain letters, digits, '.', '_', '-' (max 64 chars)."
            )
            return
        self.accept()

    def values(self) -> tuple[str, dict]:
        api_key = self._api_key_field.text().strip()
        token_limits = self._pending_token_limits
        return self._name_field.text().strip(), {
            "model": self._model_field.text().strip(),
            "base_url": self._base_url_field.text().strip() or None,
            "api_key": api_key or None,
            "preserve_existing_api_key": self._editing and not api_key,
            "temperature": self._temperature.value(),
            "top_p": self._top_p.value(),
            "top_k": self._top_k.value(),
            "max_input_tokens": token_limits.get("max_input_tokens"),
            "max_output_tokens": token_limits.get("max_output_tokens"),
            "stream": True,
            "caching_prompt": False,
            "native_tool_calling": True,
            "reasoning_effort": None,
            "enable_encrypted_reasoning": False,
            "prompt_cache_retention": None,
            "extended_thinking_budget": None,
            "litellm_extra_body": {
                "min_p": self._min_p.value(),
                "presence_penalty": self._presence_penalty.value(),
                "repeat_penalty": 1.0,
                # DRY sampler (llama.cpp): penalizes repeated *sequences*
                # exponentially by match length, which is what actually
                # catches a model re-treading the same reasoning/search
                # pattern -- see the _LLM_PRESETS comment above for why
                # repeat_penalty alone (token-level only) doesn't.
                "dry_multiplier": 0.8,
                "dry_base": 1.75,
                "dry_allowed_length": 2,
                "chat_template_kwargs": {
                    "enable_thinking": self._enable_thinking.isChecked(),
                    "preserve_thinking": self._preserve_thinking.isChecked(),
                },
            },
            "extra_config": self._detail,
        }


class _ProfileRowWidget(QWidget):
    """One LLM profile row: name/model/base_url on the left; on the right,
    either a green "active" checkmark or a real Activate button, plus real
    Edit (opens _ProfileFormDialog pre-filled) and Delete (confirms, then
    calls the server) -- all backed by the real
    /api/v1/settings/profiles/{name} endpoints."""

    activate_clicked = Signal(str)  # profile name
    save_requested = Signal(str, dict)  # profile name, fields
    delete_requested = Signal(str)  # profile name
    edit_clicked = Signal(str)  # profile name -- _LlmPage fetches full detail before opening the form

    def __init__(self, profile: LlmProfile, active: bool) -> None:
        super().__init__()
        self._profile = profile
        self.setObjectName("SettingsProfileRow")
        self._row_layout = QHBoxLayout(self)
        self._row_layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self._name_label = QLabel()
        self._name_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        text_col.addWidget(self._name_label)
        detail = profile.model + (f" · {profile.base_url}" if profile.base_url else "")
        detail_label = QLabel(detail)
        detail_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        text_col.addWidget(detail_label)
        self._row_layout.addLayout(text_col, 1)

        self._active_icon = QLabel()
        self._active_icon.setPixmap(icon("tests-check", 16).pixmap(16, 16))
        self._row_layout.addWidget(self._active_icon)

        self._activate_btn = QPushButton("Activate")
        self._activate_btn.clicked.connect(lambda: self.activate_clicked.emit(self._profile.name))
        self._row_layout.addWidget(self._activate_btn)

        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._on_edit_clicked)
        self._row_layout.addWidget(edit_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._on_delete_clicked)
        self._row_layout.addWidget(delete_btn)

        self.set_active(active)

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(
            f"#SettingsProfileRow {{ background-color: {BG_SURFACE_2}; "
            f"border: 1px solid {COLOR_PRIMARY if active else BORDER}; border-radius: {RADIUS_MD}px; }}"
        )
        self._name_label.setText(self._profile.name + (" (active)" if active else ""))
        self._active_icon.setVisible(active)
        self._activate_btn.setVisible(not active)

    def _on_edit_clicked(self) -> None:
        self.edit_clicked.emit(self._profile.name)

    def open_edit_dialog(self, detail: dict) -> None:
        dialog = _ProfileFormDialog(self, profile=self._profile, detail=detail)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, fields = dialog.values()
            self.save_requested.emit(name, fields)

    def _on_delete_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "Delete profile",
            f"Delete profile '{self._profile.name}'? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(self._profile.name)


class _LlmPage(QWidget):
    activate_requested = Signal(str)  # profile name -- MainWindow syncs the toolbar combo to this
    profiles_changed = Signal()
    default_plan_model_changed = Signal(str)  # profile name, "" clears it
    default_code_model_changed = Signal(str)  # profile name, "" clears it
    auto_decide_plan_code_changed = Signal(bool)
    lm_studio_context_length_changed = Signal(int)

    def __init__(
        self,
        client: AppServerClient,
        profiles: list[LlmProfile],
        active_profile_name: str | None,
        *,
        default_plan_model: str | None = None,
        default_code_model: str | None = None,
        auto_decide_plan_code: bool = True,
        lm_studio_context_length: int = 131072,
    ) -> None:
        super().__init__()
        self._client = client
        self._active_name = active_profile_name
        self._rows: dict[str, _ProfileRowWidget] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page, self._layout = _page("LLM", "llm", "Language model profiles available to this workspace.")
        outer.addWidget(self._page)

        self._active_label = QLabel()
        self._active_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self._update_active_label()
        self._layout.addWidget(self._active_label)

        # Client-only convenience (not a server concept): auto-switch the
        # Model chip when starting a Plan conversation or when using
        # Continue-as-Code, instead of requiring a manual switch each time.
        defaults_form = _form(self._layout)
        self._plan_model_combo = QComboBox()
        self._plan_model_combo.addItem("(ask each time)", userData="")
        self._code_model_combo = QComboBox()
        self._code_model_combo.addItem("(ask each time)", userData="")
        for profile in profiles:
            self._plan_model_combo.addItem(profile.name, userData=profile.name)
            self._code_model_combo.addItem(profile.name, userData=profile.name)
        if default_plan_model:
            idx = self._plan_model_combo.findData(default_plan_model)
            if idx >= 0:
                self._plan_model_combo.setCurrentIndex(idx)
        if default_code_model:
            idx = self._code_model_combo.findData(default_code_model)
            if idx >= 0:
                self._code_model_combo.setCurrentIndex(idx)
        self._plan_model_combo.currentIndexChanged.connect(
            lambda: self.default_plan_model_changed.emit(self._plan_model_combo.currentData() or "")
        )
        self._code_model_combo.currentIndexChanged.connect(
            lambda: self.default_code_model_changed.emit(self._code_model_combo.currentData() or "")
        )
        defaults_form.addRow("Default Plan model", self._plan_model_combo)
        defaults_form.addRow("Default Code model", self._code_model_combo)

        self._auto_decide_check = QCheckBox("Auto-decide Plan/Code (toolbar checkbox mirrors this)")
        self._auto_decide_check.setToolTip(
            "Before starting a new conversation, ask the model whether the task needs "
            "a Plan first or can go straight to Code, instead of picking manually."
        )
        self._auto_decide_check.setChecked(auto_decide_plan_code)
        self._auto_decide_check.toggled.connect(self.auto_decide_plan_code_changed.emit)
        defaults_form.addRow("", self._auto_decide_check)

        self._context_length = QSpinBox()
        self._context_length.setRange(2048, 262144)
        self._context_length.setSingleStep(1024)
        self._context_length.setValue(lm_studio_context_length)
        self._context_length.setToolTip(
            "--context-length passed to LM Studio whenever this app loads a model itself "
            "(Plan/Code switch, Continue as Code, activating a profile). Must be >= the "
            "profiles' max_input_tokens/condenser.max_tokens or requests get rejected -- "
            "confirmed live 2026-07-31: a 49316-token request failed against a model "
            "loaded with only 32768 available. Doesn't affect an already-loaded model; "
            "only takes effect on the next swap this app triggers."
        )
        # editingFinished, not valueChanged -- the latter fires on every
        # single step/keystroke, which would fire off the (several API
        # calls deep) profile-sync chain on the far end for every click of
        # the spinner arrows instead of once when the user is actually done.
        self._context_length.editingFinished.connect(
            lambda: self.lm_studio_context_length_changed.emit(self._context_length.value())
        )
        defaults_form.addRow("LM Studio context length", self._context_length)

        defaults_note = QLabel(
            "When set, switching to Plan (new conversation) or clicking Continue as Code "
            "automatically switches the toolbar's Model chip to these -- local preference only."
        )
        defaults_note.setWordWrap(True)
        defaults_note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self._layout.addWidget(defaults_note)

        self._rows_container = QVBoxLayout()
        self._layout.addLayout(self._rows_container)

        add_btn = QPushButton("Add profile…")
        add_btn.clicked.connect(self._on_add_clicked)
        self._layout.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)

        note = QLabel(
            "Profiles are configured on the OpenHands server. Activating one here has the "
            "same effect as picking it from the Model chip in the toolbar."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self._layout.addWidget(note)
        self._layout.addStretch(1)

        self._render(profiles)

    def _render(self, profiles: list[LlmProfile]) -> None:
        while self._rows_container.count():
            item = self._rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows = {}
        if not profiles:
            empty = QLabel("No LLM profiles loaded yet.")
            empty.setStyleSheet(f"color: {TEXT_MUTED};")
            self._rows_container.addWidget(empty)
            return
        for profile in profiles:
            row = _ProfileRowWidget(profile, profile.name == self._active_name)
            row.activate_clicked.connect(self._on_activate_clicked)
            row.save_requested.connect(self._on_save)
            row.delete_requested.connect(self._on_delete)
            row.edit_clicked.connect(self._on_edit_clicked)
            self._rows[profile.name] = row
            self._rows_container.addWidget(row)

    def _on_edit_clicked(self, name: str) -> None:
        row = self._rows.get(name)
        if row is None:
            return
        _run_async(
            self,
            self._client.get_profile_detail(name),
            lambda data: row.open_edit_dialog(data.get("config") or {}),
            error_title="Failed to load profile details",
        )

    def _reload(self) -> None:
        async def _fetch():
            return await self._client.list_llm_profiles()

        def _on_done(result):
            profiles, active = result
            self._active_name = active
            self._update_active_label()
            self._render(profiles)
            self.profiles_changed.emit()

        _run_async(self, _fetch(), _on_done, error_title="Failed to reload profiles")

    def _update_active_label(self) -> None:
        text = f"Toolbar's active model right now: {self._active_name}" if self._active_name else "No model is currently active."
        self._active_label.setText(text)

    def _on_activate_clicked(self, name: str) -> None:
        if name == self._active_name:
            return
        _run_async(
            self,
            self._client.activate_profile(name),
            lambda _: (self.activate_requested.emit(name), self._reload()),
            error_title="Failed to activate profile",
        )

    def _on_add_clicked(self) -> None:
        dialog = _ProfileFormDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, fields = dialog.values()
            self._on_save(name, fields)

    def _on_save(self, name: str, fields: dict) -> None:
        async def _save_and_activate() -> None:
            await self._client.save_profile(name, **fields)
            await self._client.activate_profile(name)

        _run_async(
            self,
            _save_and_activate(),
            lambda _: (self.activate_requested.emit(name), self._reload()),
            error_title="Failed to save profile",
        )

    def _on_delete(self, name: str) -> None:
        _run_async(self, self._client.delete_profile(name), lambda _: self._reload(), error_title="Failed to delete profile")


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------


class _AgentPage(QWidget):
    auto_supervise_changed = Signal(bool)

    def __init__(
        self, client: AppServerClient, agent_settings: dict, *, auto_supervise: bool = True
    ) -> None:
        super().__init__()
        self._client = client
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = _page("Agent", "agent", "Agent behavior for new conversations.")
        outer.addWidget(page)
        form = _form(layout)

        self._agent_field = QLineEdit(agent_settings.get("agent") or "CodeActAgent")
        form.addRow("Agent class", self._agent_field)

        self._sub_agents = QCheckBox("Enable sub-agent delegation")
        self._sub_agents.setChecked(bool(agent_settings.get("enable_sub_agents")))
        form.addRow("", self._sub_agents)

        self._switch_llm = QCheckBox("Enable the switch_llm tool")
        self._switch_llm.setChecked(bool(agent_settings.get("enable_switch_llm_tool")))
        form.addRow("", self._switch_llm)

        self._concurrency = QSpinBox()
        self._concurrency.setRange(1, 16)
        self._concurrency.setValue(int(agent_settings.get("tool_concurrency_limit") or 1))
        form.addRow("Parallel tool calls", self._concurrency)

        agent_context = agent_settings.get("agent_context") or {}
        self._instructions = QPlainTextEdit(agent_context.get("system_message_suffix") or "")
        self._instructions.setPlaceholderText("e.g. Always write tests before implementing a feature.")
        self._instructions.setFixedHeight(80)
        form.addRow("Custom instructions", self._instructions)

        # Client-only preference (not a server concept, so not part of the
        # Save button's agent_settings_diff below) -- takes effect instantly
        # on toggle, same as the toolbar's own auto-behavior checkboxes.
        self._auto_supervise_check = QCheckBox("Auto-supervise conversations")
        self._auto_supervise_check.setToolTip(
            "Watch every conversation's live event stream for repeated tool calls "
            "(same tool, same arguments) -- nudge once when it happens, and stop "
            "(interrupt only) if the agent repeats it again after that. Runs "
            "automatically on every new conversation; turn off to rely only on "
            "the SDK's own (less configurable) stuck detector instead."
        )
        self._auto_supervise_check.setChecked(auto_supervise)
        self._auto_supervise_check.toggled.connect(self.auto_supervise_changed.emit)
        form.addRow("", self._auto_supervise_check)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _on_save(self) -> None:
        payload = {
            "agent_settings_diff": {
                "agent": self._agent_field.text().strip() or "CodeActAgent",
                "enable_sub_agents": self._sub_agents.isChecked(),
                "enable_switch_llm_tool": self._switch_llm.isChecked(),
                "tool_concurrency_limit": self._concurrency.value(),
                "agent_context": {"system_message_suffix": self._instructions.toPlainText().strip() or None},
            }
        }
        _run_async(
            self, self._client.update_settings(payload), lambda _: _saved_flash(self._save_btn),
            error_title="Failed to save agent settings",
        )


# --------------------------------------------------------------------------
# Condenser
# --------------------------------------------------------------------------


class _CondenserPage(QWidget):
    def __init__(self, client: AppServerClient, agent_settings: dict) -> None:
        super().__init__()
        self._client = client
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = _page(
            "Condenser", "condenser", "Control how conversation history is summarized to fit the context window."
        )
        outer.addWidget(page)
        form = _form(layout)

        condenser = agent_settings.get("condenser") or {}

        self._enabled = QCheckBox("Enabled")
        self._enabled.setChecked(bool(condenser.get("enabled", True)))
        form.addRow("", self._enabled)

        self._kind = QComboBox()
        self._kind.addItems(["llm_summarizing", "no_op"])
        kind = condenser.get("condenser_kind", "llm_summarizing")
        if kind in ("llm_summarizing", "no_op"):
            self._kind.setCurrentText(kind)
        form.addRow("Strategy", self._kind)

        self._max_size = QSpinBox()
        self._max_size.setRange(10, 2000)
        self._max_size.setValue(int(condenser.get("max_size", 240)))
        self._max_size.setSuffix(" events")
        form.addRow("Max history size", self._max_size)

        self._keep_first = QSpinBox()
        self._keep_first.setRange(0, 50)
        self._keep_first.setValue(int(condenser.get("keep_first", 2)))
        form.addRow("Always keep first N events", self._keep_first)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _on_save(self) -> None:
        payload = {
            "agent_settings_diff": {
                "condenser": {
                    "enabled": self._enabled.isChecked(),
                    "condenser_kind": self._kind.currentText(),
                    "max_size": self._max_size.value(),
                    "keep_first": self._keep_first.value(),
                }
            }
        }
        _run_async(
            self, self._client.update_settings(payload), lambda _: _saved_flash(self._save_btn),
            error_title="Failed to save condenser settings",
        )


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


class _VerificationPage(QWidget):
    def __init__(self, client: AppServerClient, conversation_settings: dict) -> None:
        super().__init__()
        self._client = client
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = _page(
            "Verification", "verification", "Guardrails and confirmation steps before the agent acts."
        )
        outer.addWidget(page)
        form = _form(layout)

        self._confirmation = QCheckBox("Confirmation mode -- ask before every risky action")
        self._confirmation.setChecked(bool(conversation_settings.get("confirmation_mode")))
        form.addRow("", self._confirmation)

        self._analyzer = QComboBox()
        self._analyzer.addItems(["llm", "none"])
        analyzer = conversation_settings.get("security_analyzer", "llm")
        if analyzer in ("llm", "none"):
            self._analyzer.setCurrentText(analyzer)
        form.addRow("Security analyzer", self._analyzer)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _on_save(self) -> None:
        payload = {
            "conversation_settings_diff": {
                "confirmation_mode": self._confirmation.isChecked(),
                "security_analyzer": self._analyzer.currentText(),
            }
        }
        _run_async(
            self, self._client.update_settings(payload), lambda _: _saved_flash(self._save_btn),
            error_title="Failed to save verification settings",
        )


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------


# Real, officially-maintained reference servers from the Model Context
# Protocol project (github.com/modelcontextprotocol/servers) -- run locally
# via npx, i.e. stdio transport. Not a live marketplace (no install counts,
# no discovery API exists for this), just a known-good quick-fill so adding
# one of these doesn't require typing the npx invocation from memory.
_KNOWN_STDIO_SERVERS = [
    ("Filesystem (official)", "filesystem", "npx", ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]),
    ("Fetch / web (official)", "fetch", "npx", ["-y", "@modelcontextprotocol/server-fetch"]),
    ("Memory (official)", "memory", "npx", ["-y", "@modelcontextprotocol/server-memory"]),
    (
        "Sequential thinking (official)",
        "sequential-thinking",
        "npx",
        ["-y", "@modelcontextprotocol/server-sequential-thinking"],
    ),
]


class _McpServerFormDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add MCP server")
        layout = QVBoxLayout(self)

        self._quick_fill = QComboBox()
        self._quick_fill.addItem("Quick-fill from a known server…")
        for label, *_rest in _KNOWN_STDIO_SERVERS:
            self._quick_fill.addItem(label)
        self._quick_fill.currentIndexChanged.connect(self._on_quick_fill)
        layout.addWidget(self._quick_fill)

        self._conn_type = QComboBox()
        self._conn_type.addItems(["Remote (URL)", "Command (stdio)"])
        self._conn_type.currentIndexChanged.connect(self._on_conn_type_changed)
        form = QFormLayout()
        form.setSpacing(SPACE_SM)
        form.addRow("Connection type", self._conn_type)
        layout.addLayout(form)

        self._name_field = QLineEdit()
        self._name_field.setPlaceholderText("e.g. my-mcp-server")
        form.addRow("Name", self._name_field)

        self._remote_group = QWidget()
        remote_form = QFormLayout(self._remote_group)
        remote_form.setContentsMargins(0, 0, 0, 0)
        remote_form.setSpacing(SPACE_SM)
        self._url_field = QLineEdit()
        self._url_field.setPlaceholderText("http://127.0.0.1:8765/mcp")
        remote_form.addRow("URL", self._url_field)
        self._transport = QComboBox()
        self._transport.addItems(["streamable-http", "sse"])
        remote_form.addRow("Transport", self._transport)
        self._token_field = QLineEdit()
        self._token_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_field.setPlaceholderText("optional bearer token")
        remote_form.addRow("Bearer token", self._token_field)
        layout.addWidget(self._remote_group)

        self._stdio_group = QWidget()
        stdio_form = QFormLayout(self._stdio_group)
        stdio_form.setContentsMargins(0, 0, 0, 0)
        stdio_form.setSpacing(SPACE_SM)
        self._command_field = QLineEdit()
        self._command_field.setPlaceholderText("e.g. npx")
        stdio_form.addRow("Command", self._command_field)
        self._args_field = QLineEdit()
        self._args_field.setPlaceholderText("space-separated, e.g. -y @modelcontextprotocol/server-fetch")
        stdio_form.addRow("Args", self._args_field)
        layout.addWidget(self._stdio_group)
        self._stdio_group.setVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_conn_type_changed(self, index: int) -> None:
        self._remote_group.setVisible(index == 0)
        self._stdio_group.setVisible(index == 1)

    def _on_quick_fill(self, index: int) -> None:
        if index == 0:
            return
        _label, name, command, args = _KNOWN_STDIO_SERVERS[index - 1]
        self._name_field.setText(name)
        self._command_field.setText(command)
        self._args_field.setText(" ".join(args))
        self._conn_type.setCurrentIndex(1)

    def _on_accept(self) -> None:
        if not self._name_field.text().strip():
            QMessageBox.warning(self, "Missing fields", "Name is required.")
            return
        if self._conn_type.currentIndex() == 0 and not self._url_field.text().strip():
            QMessageBox.warning(self, "Missing fields", "URL is required for a remote server.")
            return
        if self._conn_type.currentIndex() == 1 and not self._command_field.text().strip():
            QMessageBox.warning(self, "Missing fields", "Command is required for a stdio server.")
            return
        self.accept()

    def values(self) -> tuple[str, dict]:
        if self._conn_type.currentIndex() == 1:
            config: dict = {
                "command": self._command_field.text().strip(),
                "args": self._args_field.text().split(),
                "transport": "stdio",
            }
            return self._name_field.text().strip(), config
        config = {"url": self._url_field.text().strip(), "transport": self._transport.currentText()}
        token = self._token_field.text().strip()
        if token:
            config["auth"] = {"strategy": "bearer", "token": token}
        return self._name_field.text().strip(), config


class _McpPage(QWidget):
    def __init__(self, client: AppServerClient, mcp_config: dict) -> None:
        super().__init__()
        self._client = client
        self._mcp_config = dict(mcp_config)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, self._layout = _page("MCP", "mcp", "Model Context Protocol servers available to the agent.")
        outer.addWidget(page)

        self._rows_container = QVBoxLayout()
        self._layout.addLayout(self._rows_container)

        add_btn = QPushButton("Add server…")
        add_btn.clicked.connect(self._on_add_clicked)
        self._layout.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._layout.addStretch(1)

        self._render()

    def _render(self) -> None:
        while self._rows_container.count():
            item = self._rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._mcp_config:
            empty = QLabel("No MCP servers configured.")
            empty.setStyleSheet(f"color: {TEXT_MUTED};")
            self._rows_container.addWidget(empty)
            return
        for name, config in self._mcp_config.items():
            row, row_layout = _row_widget()
            text_col = QVBoxLayout()
            text_col.setSpacing(0)
            name_label = QLabel(name)
            name_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
            text_col.addWidget(name_label)
            if config.get("transport") == "stdio":
                detail = f"{config.get('command', '?')} {' '.join(config.get('args', []))}".strip()
            else:
                detail = f"{config.get('url', '?')} · {config.get('transport', '?')}"
            detail_label = QLabel(detail)
            detail_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
            text_col.addWidget(detail_label)
            row_layout.addLayout(text_col, 1)
            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _checked=False, n=name: self._on_remove(n))
            row_layout.addWidget(remove_btn)
            self._rows_container.addWidget(row)

    def _save_full_config(self, new_config: dict, on_success) -> None:
        # mcp_config is replaced wholesale by the server, not merged per-entry
        # (confirmed live) -- always send the complete resulting dict.
        payload = {"agent_settings_diff": {"mcp_config": new_config}}
        _run_async(self, self._client.update_settings(payload), on_success, error_title="Failed to save MCP servers")

    def _on_add_clicked(self) -> None:
        dialog = _McpServerFormDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, config = dialog.values()
            new_config = dict(self._mcp_config)
            new_config[name] = config
            self._save_full_config(new_config, lambda _: self._on_saved(new_config))

    def _on_remove(self, name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Remove MCP server",
            f"Remove MCP server '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        new_config = dict(self._mcp_config)
        new_config.pop(name, None)
        self._save_full_config(new_config, lambda _: self._on_saved(new_config))

    def _on_saved(self, new_config: dict) -> None:
        self._mcp_config = new_config
        self._render()


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------


class _SkillsPage(QWidget):
    def __init__(self, client: AppServerClient, skills: list[dict], disabled_skills: list[str]) -> None:
        super().__init__()
        self._client = client
        self._disabled = set(disabled_skills)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = _page("Skills", "skills", "Microagents / skills the agent can use.")
        outer.addWidget(page)

        self._list = QListWidget()
        for s in skills:
            name = s.get("name", "?")
            source = s.get("source", "")
            item = QListWidgetItem(f"{name}  ({source})" if source else name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if name in self._disabled else Qt.CheckState.Checked)
            self._list.addItem(item)
        if not skills:
            empty_item = QListWidgetItem("No skills found.")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty_item)
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

        note = QLabel("Unchecking a skill disables it globally (agent_context.disabled_skills).")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(note)
        layout.addStretch(1)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        new_disabled = set(self._disabled)
        if enabled:
            new_disabled.discard(name)
        else:
            new_disabled.add(name)

        payload = {"agent_settings_diff": {"agent_context": {"disabled_skills": sorted(new_disabled)}}}
        # Disable the whole list rather than flipping the ItemIsUserCheckable
        # bit on this one item while saving: PySide6's Qt.ItemFlag is an
        # IntFlag-like enum whose `~` operator recurses infinitely on some
        # versions (confirmed live -- crashed with RecursionError), so it's
        # avoided entirely rather than fought with.
        self._list.setEnabled(False)

        def _on_success(_):
            self._disabled = new_disabled
            self._list.setEnabled(True)

        def _on_error():
            self._list.blockSignals(True)
            item.setCheckState(Qt.CheckState.Unchecked if not enabled else Qt.CheckState.Checked)
            self._list.blockSignals(False)
            self._list.setEnabled(True)

        async def _do():
            try:
                await self._client.update_settings(payload)
            except Exception:
                _on_error()
                raise
            return None

        _run_async(self, _do(), _on_success, error_title="Failed to update skill")


# --------------------------------------------------------------------------
# Integrations
# --------------------------------------------------------------------------


class _IntegrationsPage(QWidget):
    """Git-provider OAuth token storage has no equivalent on the new Agent
    Server (checked its full OpenAPI schema -- not present under any path,
    unlike profiles/settings/skills/secrets which all have close
    successors). Disabled rather than wired to client methods that don't
    exist -- see MIGRATION_STATUS.md."""

    def __init__(self, client: AppServerClient, provider_tokens_set: dict) -> None:
        super().__init__()
        self._client = client
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = _page("Integrations", "integrations", "Connect git provider accounts.")
        outer.addWidget(page)

        note = QLabel(
            "Not available yet on this server: the new Agent Server (v1.1's "
            "backend, see MIGRATION_STATUS.md) has no git-provider token "
            "storage endpoint at all -- this section is disabled until "
            "that lands upstream or a local equivalent is added."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(note)
        layout.addStretch(1)


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


class _ApplicationPage(QWidget):
    def __init__(self, client: AppServerClient, settings: dict) -> None:
        super().__init__()
        self._client = client
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = _page("Application", "application", "General application preferences.")
        outer.addWidget(page)
        form = _form(layout)

        self._language = QComboBox()
        for label, _code in _LANGUAGE_CODES:
            self._language.addItem(label)
        current_lang = settings.get("language")
        for i, (_label, code) in enumerate(_LANGUAGE_CODES):
            if code == current_lang:
                self._language.setCurrentIndex(i)
                break
        form.addRow("Language", self._language)

        theme = QComboBox()
        theme.addItems(["Dark"])
        theme.setEnabled(False)
        theme.setToolTip("Only the dark theme is implemented so far.")
        form.addRow("Theme", theme)

        self._sound = QCheckBox("Play a sound when the agent finishes")
        self._sound.setChecked(bool(settings.get("enable_sound_notifications")))
        form.addRow("", self._sound)

        self._proactive = QCheckBox("Show proactive conversation starters")
        self._proactive.setChecked(bool(settings.get("enable_proactive_conversation_starters", True)))
        form.addRow("", self._proactive)

        self._analytics = QCheckBox("Send anonymous usage analytics")
        self._analytics.setChecked(bool(settings.get("user_consents_to_analytics")))
        form.addRow("", self._analytics)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _on_save(self) -> None:
        _label, code = _LANGUAGE_CODES[self._language.currentIndex()]
        payload = {
            "language": code,
            "enable_sound_notifications": self._sound.isChecked(),
            "enable_proactive_conversation_starters": self._proactive.isChecked(),
            "user_consents_to_analytics": self._analytics.isChecked(),
        }
        _run_async(
            self, self._client.update_settings(payload), lambda _: _saved_flash(self._save_btn),
            error_title="Failed to save application settings",
        )


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------


class _SecretFormDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, editing: dict | None = None) -> None:
        super().__init__(parent)
        self._editing = editing is not None
        self.setWindowTitle("Edit secret" if self._editing else "Add secret")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(SPACE_SM)
        layout.addLayout(form)

        self._name_field = QLineEdit(editing.get("name", "") if editing else "")
        self._name_field.setPlaceholderText("e.g. MY_API_KEY")
        form.addRow("Name", self._name_field)

        self._value_field = QLineEdit()
        self._value_field.setEchoMode(QLineEdit.EchoMode.Password)
        if self._editing:
            self._value_field.setEnabled(False)
            self._value_field.setPlaceholderText("value can't be changed -- delete and re-add instead")
        else:
            self._value_field.setPlaceholderText("secret value")
        form.addRow("Value", self._value_field)

        self._description_field = QLineEdit(editing.get("description") or "" if editing else "")
        self._description_field.setPlaceholderText("optional")
        form.addRow("Description", self._description_field)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self._name_field.text().strip():
            QMessageBox.warning(self, "Missing name", "Name is required.")
            return
        if not self._editing and not self._value_field.text().strip():
            QMessageBox.warning(self, "Missing value", "Value is required.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self._name_field.text().strip(),
            "value": self._value_field.text().strip(),
            "description": self._description_field.text().strip() or None,
        }


class _SecretsPage(QWidget):
    def __init__(self, client: AppServerClient, secrets: list[dict]) -> None:
        super().__init__()
        self._client = client
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, self._layout = _page(
            "Secrets", "secrets", "Custom secrets exposed to the agent as environment variables."
        )
        outer.addWidget(page)

        self._rows_container = QVBoxLayout()
        self._layout.addLayout(self._rows_container)

        add_btn = QPushButton("Add secret…")
        add_btn.clicked.connect(self._on_add_clicked)
        self._layout.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._layout.addStretch(1)

        self._render(secrets)

    def _render(self, secrets: list[dict]) -> None:
        while self._rows_container.count():
            item = self._rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not secrets:
            empty = QLabel("No custom secrets configured.")
            empty.setStyleSheet(f"color: {TEXT_MUTED};")
            self._rows_container.addWidget(empty)
            return
        for secret in secrets:
            row, row_layout = _row_widget()
            text_col = QVBoxLayout()
            text_col.setSpacing(0)
            name_label = QLabel(secret["name"])
            name_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
            text_col.addWidget(name_label)
            if secret.get("description"):
                desc_label = QLabel(secret["description"])
                desc_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
                text_col.addWidget(desc_label)
            row_layout.addLayout(text_col, 1)
            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _c=False, s=secret: self._on_edit_clicked(s))
            row_layout.addWidget(edit_btn)
            delete_btn = QPushButton("Delete")
            delete_btn.clicked.connect(lambda _c=False, n=secret["name"]: self._on_delete(n))
            row_layout.addWidget(delete_btn)
            self._rows_container.addWidget(row)

    def _reload(self) -> None:
        _run_async(self, self._client.list_secrets(), self._render, error_title="Failed to reload secrets")

    def _on_add_clicked(self) -> None:
        dialog = _SecretFormDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.values()
            _run_async(
                self,
                self._client.create_secret(values["name"], values["value"], values["description"]),
                lambda _: self._reload(),
                error_title="Failed to create secret",
            )

    def _on_edit_clicked(self, secret: dict) -> None:
        # The new server's secrets endpoint is PUT-to-create-or-update by
        # name with a REQUIRED value field (verified live) -- there's no
        # metadata-only update route like the old app-server's
        # PUT /api/v1/secrets/{secret_id} had. The dialog's own value field
        # is already disabled in edit mode with a "delete and re-add
        # instead" placeholder; this now actually follows through on that
        # instead of calling an update that would either fail (empty
        # required field) or silently blank out the real secret value.
        QMessageBox.information(
            self,
            "Can't edit in place",
            "Secrets can't be edited in place on this server -- delete this one "
            "and add it again with the new value.",
        )

    def _on_delete(self, name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete secret",
            f"Delete secret '{name}'? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            _run_async(self, self._client.delete_secret(name), lambda _: self._reload(), error_title="Failed to delete secret")


# --------------------------------------------------------------------------
# Nav + dialog shell
# --------------------------------------------------------------------------


class _NavItemWidget(QWidget):
    def __init__(self, title: str, icon_name: str) -> None:
        super().__init__()
        self._icon_name = icon_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        layout.setSpacing(SPACE_SM)
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(18, 18)
        layout.addWidget(self._icon_label)
        self._title_label = QLabel(title)
        layout.addWidget(self._title_label, 1)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        color = COLOR_PRIMARY if active else TEXT_SECONDARY
        self._icon_label.setPixmap(menu_icon(self._icon_name, 18, color).pixmap(18, 18))
        self._title_label.setStyleSheet(
            f"color: {COLOR_PRIMARY if active else TEXT_PRIMARY}; "
            f"font-weight: {600 if active else 400};"
        )


class SettingsDialog(QDialog):
    profile_activate_requested = Signal(str)  # profile name -- MainWindow syncs the toolbar Model chip to this
    profiles_changed = Signal()
    default_plan_model_changed = Signal(str)
    default_code_model_changed = Signal(str)
    auto_decide_plan_code_changed = Signal(bool)
    lm_studio_context_length_changed = Signal(int)
    auto_supervise_changed = Signal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        client: AppServerClient,
        settings: dict,
        profiles: list[LlmProfile] | None = None,
        active_profile_name: str | None = None,
        default_plan_model: str | None = None,
        default_code_model: str | None = None,
        auto_decide_plan_code: bool = True,
        lm_studio_context_length: int = 131072,
        auto_supervise: bool = True,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("Settings")
        self.resize(760, 520)
        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {BG_SURFACE_1}; }}
            #SettingsNav {{
                background-color: {BG_SURFACE_1};
                border: none;
                outline: none;
            }}
            #SettingsNav::item {{
                border-radius: {RADIUS_MD}px;
                margin: 1px {SPACE_XS}px;
            }}
            #SettingsNav::item:selected {{
                background-color: {BG_SURFACE_2};
            }}
            #SettingsNav::item:hover:!selected {{
                background-color: rgba(255, 255, 255, 12);
            }}
            #SettingsContent {{
                background-color: {BG_SURFACE_1};
                border-left: 1px solid {BORDER};
            }}
            QListWidget {{
                background-color: {BG_SURFACE_2};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_MD}px;
            }}
            """
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav = QListWidget()
        self._nav.setObjectName("SettingsNav")
        self._nav.setFixedWidth(200)
        self._nav.setSpacing(0)
        root.addWidget(self._nav)

        content_wrapper = QWidget()
        content_wrapper.setObjectName("SettingsContent")
        content_layout = QVBoxLayout(content_wrapper)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack)
        root.addWidget(content_wrapper, 1)

        agent_settings = settings.get("agent_settings") or {}
        conversation_settings = settings.get("conversation_settings") or {}
        agent_context = agent_settings.get("agent_context") or {}

        self._llm_page = _LlmPage(
            client,
            profiles or [],
            active_profile_name,
            default_plan_model=default_plan_model,
            default_code_model=default_code_model,
            auto_decide_plan_code=auto_decide_plan_code,
            lm_studio_context_length=lm_studio_context_length,
        )
        self._llm_page.activate_requested.connect(self.profile_activate_requested.emit)
        self._llm_page.profiles_changed.connect(self.profiles_changed.emit)
        self._llm_page.default_plan_model_changed.connect(self.default_plan_model_changed.emit)
        self._llm_page.default_code_model_changed.connect(self.default_code_model_changed.emit)
        self._llm_page.auto_decide_plan_code_changed.connect(self.auto_decide_plan_code_changed.emit)
        self._llm_page.lm_studio_context_length_changed.connect(self.lm_studio_context_length_changed.emit)

        self._agent_page = _AgentPage(client, agent_settings, auto_supervise=auto_supervise)
        self._agent_page.auto_supervise_changed.connect(self.auto_supervise_changed.emit)

        pages = {
            "Agent": lambda: self._agent_page,
            "LLM": lambda: self._llm_page,
            "Condenser": lambda: _CondenserPage(client, agent_settings),
            "Verification": lambda: _VerificationPage(client, conversation_settings),
            "MCP": lambda: _McpPage(client, agent_settings.get("mcp_config") or {}),
            "Skills": lambda: _SkillsPageAsync(client, agent_context.get("disabled_skills") or []),
            "Integrations": lambda: _IntegrationsPage(client, settings.get("provider_tokens_set") or {}),
            "Application": lambda: _ApplicationPage(client, settings),
            "Secrets": lambda: _SecretsPageAsync(client),
        }

        self._nav_widgets: list[_NavItemWidget] = []
        for title in _SECTIONS:
            item = QListWidgetItem(self._nav)
            nav_widget = _NavItemWidget(title, _ICON_FOR[title])
            item.setSizeHint(nav_widget.sizeHint())
            self._nav.setItemWidget(item, nav_widget)
            self._nav_widgets.append(nav_widget)
            self._stack.addWidget(pages[title]())

        self._nav.currentRowChanged.connect(self._on_row_changed)
        self._nav.setCurrentRow(0)

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        for i, widget in enumerate(self._nav_widgets):
            widget.set_active(i == row)
        self._stack.setCurrentIndex(row)


def _SkillsPageAsync(client: AppServerClient, disabled_skills: list[str]) -> QWidget:
    """Skills need one extra network call (list_skills) beyond the single
    already-loaded /api/v1/settings blob every other page uses -- shows a
    loading placeholder, then swaps in the real _SkillsPage once it lands."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    page, inner_layout = _page("Skills", "skills", "Microagents / skills the agent can use.")
    loading = QLabel("Loading skills…")
    loading.setStyleSheet(f"color: {TEXT_MUTED};")
    inner_layout.addWidget(loading)
    inner_layout.addStretch(1)
    layout.addWidget(page)

    def _on_loaded(skills: list[dict]) -> None:
        real_page = _SkillsPage(client, skills, disabled_skills)
        layout.replaceWidget(page, real_page)
        page.deleteLater()

    _run_async(container, client.list_skills(), _on_loaded, error_title="Failed to load skills")
    return container


def _SecretsPageAsync(client: AppServerClient) -> QWidget:
    """Same pattern as _SkillsPageAsync -- secrets need their own list call."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    page, inner_layout = _page(
        "Secrets", "secrets", "Custom secrets exposed to the agent as environment variables."
    )
    loading = QLabel("Loading secrets…")
    loading.setStyleSheet(f"color: {TEXT_MUTED};")
    inner_layout.addWidget(loading)
    inner_layout.addStretch(1)
    layout.addWidget(page)

    def _on_loaded(secrets: list[dict]) -> None:
        real_page = _SecretsPage(client, secrets)
        layout.replaceWidget(page, real_page)
        page.deleteLater()

    _run_async(container, client.list_secrets(), _on_loaded, error_title="Failed to load secrets")
    return container
