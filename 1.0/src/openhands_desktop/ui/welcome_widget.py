"""Empty-state hero panel shown before any conversation has started.

Icons come from the real "OpenHands Neon SVG Kit" (resources/icons/,
see icons.py) -- no emoji, no hand-approximated shapes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from openhands_desktop.ui.icons import icon, tile_icon
from openhands_desktop.ui.spacing import ICON_HERO, SPACE_LG, SPACE_MD, SPACE_SM

# Note: the kit's own code.svg / run-task.svg are both blue, not
# green/blue as the design brief's prose separately suggested -- kept as
# shipped in the kit rather than overridden, since that keeps this panel to
# one dominant accent color (blue) plus a single purple accent (history),
# matching the brief's own "don't use more than one dominant accent color
# per component" rule better than forcing a third color would.
_CARDS = [
    ("run-task", "Run tasks", "Ask the agent to build features, fix bugs, and refactor code."),
    ("code", "Work in your workspace", "The agent can read, write, and run commands in the sandbox."),
    ("history", "History & context", "Conversations are recorded locally and easy to revisit."),
]

# (icon name, button text) -- 3 chips matching the target mockup, not 6.
_SUGGESTIONS = [
    ("code", "Create a new REST API endpoint"),
    ("tests-check", "Fix failing unit tests"),
    ("refresh", "Refactor this module"),
]


class WelcomeWidget(QWidget):
    suggestion_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)

        panel = QFrame()
        panel.setObjectName("HeroPanel")
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.addStretch(1)

        logo = QLabel()
        logo.setFixedSize(ICON_HERO, ICON_HERO)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(tile_icon("terminal", ICON_HERO).pixmap(ICON_HERO, ICON_HERO))
        logo_row = QHBoxLayout()
        logo_row.addStretch()
        logo_row.addWidget(logo)
        logo_row.addStretch()
        layout.addLayout(logo_row)
        layout.addSpacing(SPACE_SM)

        title = QLabel("Welcome to OpenHands")
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Your AI agent is ready to help you build, debug, and ship.")
        subtitle.setObjectName("WelcomeSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(SPACE_LG)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(SPACE_MD)
        for icon_name, card_title, body in _CARDS:
            cards_row.addWidget(_build_card(icon_name, card_title, body))
        layout.addLayout(cards_row)

        layout.addSpacing(SPACE_LG)
        layout.addWidget(_divided_label("Get started"))
        layout.addSpacing(SPACE_SM)

        suggestions_row = QHBoxLayout()
        suggestions_row.setSpacing(SPACE_SM)
        suggestions_row.addStretch()
        for icon_name, text in _SUGGESTIONS:
            btn = QPushButton(text)
            btn.setObjectName("SuggestionButton")
            btn.setIcon(icon(icon_name, 15))
            btn.clicked.connect(lambda _checked=False, t=text: self.suggestion_clicked.emit(t))
            suggestions_row.addWidget(btn)
        suggestions_row.addStretch()
        layout.addLayout(suggestions_row)

        layout.addStretch(2)


def _divided_label(text: str) -> QWidget:
    """'Get started' centered with a thin horizontal rule on either side,
    matching the target mockup (not just plain centered text)."""
    row = QWidget()
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.setSpacing(SPACE_MD)

    def _rule() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("StatusSeparator")
        line.setFixedHeight(1)
        return line

    label = QLabel(text)
    label.setObjectName("WelcomeSubtitle")
    row_layout.addWidget(_rule(), 1)
    row_layout.addWidget(label)
    row_layout.addWidget(_rule(), 1)
    return row


def _build_card(icon_name: str, title: str, body: str) -> QWidget:
    card = QWidget()
    card.setObjectName("WelcomeCard")
    card.setMinimumWidth(220)
    # QHBoxLayout doesn't reliably honor heightForWidth for wrapped QLabels
    # (a known Qt limitation), so the card's natural sizeHint can end up
    # shorter than the wrapped body text actually needs, clipping the last
    # line -- worse, how much shorter depends on the actual rendered width,
    # which varies with window size, so a generous *minimum* height alone
    # isn't reliable either. Fixed height + a Fixed vertical size policy
    # stops the layout from trying to compute this at all.
    card.setFixedHeight(190)
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(23, 23, 23, 23)
    card_layout.setSpacing(SPACE_SM)

    # Bare glyph, not a tile: the target mockup shows flat colored icons on
    # feature cards with no extra dark background square behind them.
    icon_label = QLabel()
    icon_label.setPixmap(icon(icon_name, 26).pixmap(26, 26))
    card_layout.addWidget(icon_label)

    title_label = QLabel(title)
    title_label.setObjectName("WelcomeCardTitle")
    card_layout.addWidget(title_label)

    body_label = QLabel(body)
    body_label.setObjectName("WelcomeCardBody")
    body_label.setWordWrap(True)
    body_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    card_layout.addWidget(body_label, 1)

    return card
