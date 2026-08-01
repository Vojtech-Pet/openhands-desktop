"""Spacing and radius tokens -- 8px grid, per the user's dark UI design spec
(2026-07-27). theme.py and main_window.py both read from here so a spacing
change can't drift between the stylesheet and the manual layout margins."""

# Grid spacing
SPACE_XXS = 4    # very tight elements
SPACE_XS = 8     # between an icon and its label
SPACE_SM = 12    # between smaller components
SPACE_MD = 16    # between cards
SPACE_LG = 24    # between sections
SPACE_XL = 32    # large content divisions

# Window / panel margins
MARGIN_WINDOW = 14  # 12-16px per spec

# Radii
RADIUS_SM = 9    # small buttons: 8-10px
RADIUS_MD = 11   # inputs: 10-12px
RADIUS_LG = 15   # cards: 14-16px
RADIUS_XL = 18   # large panels: 16-20px

# Component sizes
SIDEBAR_MIN_WIDTH = 280
SIDEBAR_MAX_WIDTH = 300         # ~300px per the target mockup
CONVERSATION_ITEM_HEIGHT = 68   # 68-76px
TOOLBAR_HEIGHT = 54             # compact desktop toolbar
TOOLBAR_CONTROL_HEIGHT = 36     # compact toolbar chips
STATUS_BAR_HEIGHT = 30          # 28-34px
COMPOSER_INPUT_HEIGHT = 130     # ~130px, more dominant per the target mockup
SUGGESTION_CHIP_HEIGHT = 38     # 36-40px
SEND_STOP_WIDTH = 118           # 110-125px

# Icon sizes
ICON_TOOLBAR = 19       # 18-20px
ICON_BUTTON = 22        # 20-24px
ICON_FEATURE_CARD = 32  # 28-36px
ICON_HERO = 72          # target mockup shows a larger, more prominent tile
