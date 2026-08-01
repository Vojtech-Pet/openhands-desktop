"""Dark theme QSS. Colors come from palette.py, spacing/radii from
spacing.py -- both are the single source of truth shared with the manual
layout code in main_window.py/sidebar.py/welcome_widget.py, so nothing here
can drift from those."""

from openhands_desktop.ui.palette import (
    BG_BASE,
    BG_SURFACE_1,
    BG_SURFACE_2,
    BG_SURFACE_3,
    BORDER,
    BORDER_HOVER,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_UNVERIFIED,
    COLOR_WARNING,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from openhands_desktop.ui.spacing import RADIUS_LG, RADIUS_MD, RADIUS_SM, RADIUS_XL

DARK_QSS = f"""
QMainWindow {{
    background-color: {BG_BASE};
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
}}

#Sidebar {{
    background-color: {BG_SURFACE_1};
    border-right: 1px solid {BORDER};
}}

#SidebarHeader {{
    font-size: 15px;
    font-weight: 600;
}}

#TopBar {{
    background-color: {BG_SURFACE_1};
    border-bottom: 1px solid {BORDER};
}}

#TopBarChip {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
#TopBarChip:hover {{
    border-color: {BORDER_HOVER};
}}

#ChipCaption {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
}}
#ChipValue {{
    color: {TEXT_PRIMARY};
    font-size: 15px;
}}

#MenuButton {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 8px;
}}

#ConversationMoreButton {{
    background-color: transparent;
    color: {TEXT_SECONDARY};
    border: none;
    border-radius: {RADIUS_SM}px;
    font-weight: 700;
    padding: 0px;
}}
#ConversationMoreButton:hover {{
    background-color: {BG_SURFACE_3};
    color: {TEXT_PRIMARY};
}}

#ConversationActionsMenu {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 4px;
    color: {TEXT_PRIMARY};
}}
#ConversationActionsMenu::item {{
    padding: 6px 14px;
    border-radius: {RADIUS_SM}px;
}}
#ConversationActionsMenu::item:selected {{
    background-color: {COLOR_DANGER};
    color: white;
}}
#MenuButton:hover {{
    background-color: {BG_SURFACE_2};
}}

#StatusBarWidget {{
    background-color: {BG_SURFACE_1};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
#StatusSeparator {{
    color: {BORDER_HOVER};
}}

#RightPanel {{
    background-color: {BG_SURFACE_1};
    border-left: 1px solid {BORDER};
}}
#MainSplitter::handle {{
    background-color: {BORDER};
    width: 1px;
}}
#MainSplitter::handle:hover {{
    background-color: {BORDER_HOVER};
}}
#ContentSplitter::handle {{
    background-color: {BORDER};
    height: 1px;
}}
#ContentSplitter::handle:hover {{
    background-color: {BORDER_HOVER};
}}
#DetailCard {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}

#IconRail {{
    background-color: {BG_BASE};
    border-right: 1px solid {BORDER};
}}
#RailBrand {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_MD}px;
}}
#RailButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_MD}px;
}}
#RailButton:hover {{
    background-color: {BG_SURFACE_2};
    border-color: {BORDER};
}}

#HeroPanel {{
    background-color: {BG_SURFACE_1};
    border-radius: {RADIUS_XL}px;
}}

#WelcomeTitle {{
    font-size: 30px;
    font-weight: 600;
}}

#WelcomeSubtitle {{
    color: {TEXT_SECONDARY};
    font-size: 15px;
}}

#WelcomeCard {{
    background-color: {BG_SURFACE_2};
    border: none;
    border-radius: {RADIUS_LG}px;
}}

#WelcomeCardTitle {{
    font-weight: 600;
    font-size: 17px;
}}

#WelcomeCardBody {{
    color: {TEXT_SECONDARY};
    font-size: 14px;
}}

#SuggestionButton {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px 14px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
#SuggestionButton:hover {{
    background-color: #283a5c;
    border-color: {COLOR_PRIMARY};
}}
#SuggestionButton:pressed {{
    background-color: {BG_SURFACE_1};
}}

#ComposerFrame {{
    background-color: {BG_SURFACE_1};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_XL}px;
}}
#ComposerFrame[focused="true"] {{
    border: 1px solid {COLOR_PRIMARY};
}}

#ComposerInput {{
    border: none;
    background: transparent;
    padding: 4px 2px;
}}

#ComposerToolButton {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 4px;
}}
#ComposerToolButton:hover {{
    background-color: {BG_SURFACE_2};
}}

#ComposerPill {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 2px 8px;
    min-height: 28px;
    max-height: 28px;
}}
#ComposerSquareButton {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    min-width: 28px;
    min-height: 28px;
    max-width: 32px;
    max-height: 28px;
}}
#ComposerSquareButton:hover {{
    border-color: {BORDER_HOVER};
    background-color: {BG_SURFACE_3};
}}
#ComposerSquareButton:disabled {{
    color: {TEXT_MUTED};
}}

QListWidget#ConversationList {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListWidget#ConversationList::item {{
    border-radius: {RADIUS_LG}px;
    padding: 0px;
    margin: 4px 0px;
}}
QListWidget#ConversationList::item:hover:!selected {{
    background-color: {BG_SURFACE_3};
}}

#ConversationItem {{
    background-color: {BG_SURFACE_2};
    border-radius: {RADIUS_LG}px;
    border: 1px solid {BORDER_HOVER};
}}
#ConversationItem[selected="true"] {{
    background-color: #223252;
    border: 2px solid {COLOR_PRIMARY};
}}
#ConversationItemTitle {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
}}
#ConversationItemMeta {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}

#SearchFrame {{
    background-color: {BG_SURFACE_1};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
#SearchFrame:hover {{
    border-color: {BORDER_HOVER};
}}
#SearchInput {{
    border: none;
    background: transparent;
    padding: 4px 0px;
}}

#ProfileChip {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
#ProfileChip:hover {{
    border-color: {BORDER_HOVER};
}}
#ProfileAvatar {{
    background-color: {COLOR_PRIMARY};
    color: white;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
}}

QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {BG_SURFACE_1};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 8px 10px;
    color: {TEXT_PRIMARY};
    font-size: 14px;
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {BORDER_HOVER};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {COLOR_PRIMARY};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{
    color: {TEXT_MUTED};
}}

QComboBox {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
}}
QComboBox:hover {{
    border-color: {BORDER_HOVER};
}}
QComboBox:disabled {{
    color: {TEXT_MUTED};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_SURFACE_2};
    color: {TEXT_PRIMARY};
    selection-background-color: {COLOR_PRIMARY};
    border: 1px solid {BORDER};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 28px 6px 10px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {BG_SURFACE_3};
}}

QPushButton {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px 14px;
    color: {TEXT_PRIMARY};
}}
QPushButton:hover {{
    background-color: {BG_SURFACE_3};
    border-color: {BORDER_HOVER};
}}
QPushButton:pressed {{
    background-color: {BG_BASE};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}

#SendButton {{
    background-color: #3B82F6;
    color: white;
    font-weight: 600;
    border: none;
    border-radius: {RADIUS_SM}px;
}}
#SendButton:hover {{
    background-color: #4d90f5;
}}
#SendButton:pressed {{
    background-color: #2f6fd8;
}}
#SendButton:disabled {{
    background-color: {BG_SURFACE_2};
    color: {TEXT_MUTED};
}}

#StopButton {{
    background-color: {BG_SURFACE_2};
    border: 1px solid {COLOR_DANGER};
    color: {COLOR_DANGER};
    border-radius: {RADIUS_LG}px;
}}
#StopButton:hover {{
    background-color: #3a2229;
}}
#StopButton:disabled {{
    border-color: {BORDER};
    color: {TEXT_MUTED};
}}

#HealthOk {{ color: {COLOR_SUCCESS}; font-weight: 600; }}
#HealthBad {{ color: {COLOR_DANGER}; font-weight: 600; }}
#ModelStatusUnknown {{ color: {COLOR_WARNING}; font-weight: 600; }}

/* Agent status pill -- background+border+radius so it reads as a badge,
   not just colored text, per the "chat header status pill" spec. */
#StateIdle, #StateRunning, #StateCompleted, #StateWarning, #StateFinishedUnverified, #StateError {{
    font-weight: 600;
    font-size: 14px;
    padding: 6px 16px;
    border-radius: {RADIUS_SM}px;
    border: 1px solid transparent;
}}
#StateIdle {{ color: {TEXT_SECONDARY}; background-color: {BG_SURFACE_2}; }}
#StateRunning {{ color: #93C5FD; background-color: #132A4D; }}
#StateCompleted {{ color: {COLOR_SUCCESS}; background-color: #103B2D; }}
#StateWarning {{ color: #FBBF4D; background-color: #3A2E12; }}
#StateFinishedUnverified {{ color: {COLOR_UNVERIFIED}; background-color: #3A3512; }}
#StateError {{ color: #FCA5A5; background-color: #3A1414; }}

#ToolCallCountWarning {{ color: {COLOR_WARNING}; font-weight: 600; }}
#ToolCallCountDanger {{ color: {COLOR_DANGER}; font-weight: 600; }}

#ContinueAsCodeButton {{
    background-color: {COLOR_SUCCESS};
    color: {BG_BASE};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 0px 10px;
    font-size: 12px;
    font-weight: 600;
}}
#ContinueAsCodeButton:hover {{
    background-color: #34D06E;
}}

#StuckNudgeButton {{
    background-color: {COLOR_WARNING};
    color: {BG_BASE};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 0px 10px;
    font-size: 12px;
    font-weight: 600;
}}
#StuckNudgeButton:hover {{
    background-color: #FBBF4D;
}}

#CompactButton {{
    background-color: transparent;
    color: {TEXT_SECONDARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 0px 10px;
    font-size: 12px;
}}
#CompactButton:hover {{
    border-color: {BORDER_HOVER};
    color: {TEXT_PRIMARY};
}}
#CompactButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
#CompactButtonActive {{
    background-color: {COLOR_WARNING};
    color: {BG_BASE};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 0px 10px;
    font-size: 12px;
    font-weight: 600;
}}
#CompactButtonActive:hover {{
    background-color: #FBBF4D;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BORDER_HOVER};
}}
"""
