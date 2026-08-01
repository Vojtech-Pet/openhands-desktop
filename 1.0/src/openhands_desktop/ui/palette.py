"""Color tokens -- single source of truth for theme.py (QSS) and icons.py
(SVG stroke color). Exact values from the user's "AI coding agent" desktop
UI design spec (2026-07-30, openhands_neon_svg_kit22), superseding the
previous 2026-07-27 palette."""

# Accent colors
COLOR_PRIMARY = "#3B82F6"       # primary actions, focus, selection, active icons
COLOR_PRIMARY_GLOW = "#5D92FF"  # bright/hover glow variant of primary
COLOR_SUCCESS = "#4CE1A8"       # health OK, agent ready, completed, success
COLOR_ACCENT = "#A78BFA"        # AI model, history, debug, git, browser, agentic extras
COLOR_WARNING = "#F59E0B"       # warning, degraded health, needs attention
COLOR_DANGER = "#EF4444"        # stop, error, failed, destructive actions

# Thinking / reasoning -- deliberately its own hue family (teal), never reused
# for tool calls or results, so reasoning is unmistakable at a glance.
COLOR_THINKING_BG = "#0E2A26"
COLOR_THINKING_BORDER = "#1A8F70"
COLOR_THINKING_ACCENT = "#51D6B5"
COLOR_THINKING_TEXT = "#D7F8EF"

# Tool-type icon color -- terminal calls get their own teal-blue, distinct
# from the thinking teal (darker/greener) and from primary blue.
COLOR_TERMINAL_ICON = "#2DD4BF"

# "Finished (unverified)" needs its own hue, clearly apart from COLOR_WARNING
# (paused/waiting/deleting) -- the spec calls out Completed vs. Finished
# unverified as a distinction that must never be ambiguous at a glance.
COLOR_UNVERIFIED = "#FACC15"

# Text
TEXT_PRIMARY = "#F5F7FA"
TEXT_SECONDARY = "#D7DFEA"
TEXT_MUTED = "#8E9AAC"

# Surfaces (darkest to lightest)
BG_BASE = "#07111E"          # window background
BG_SURFACE_1 = "#0B1625"     # sidebar / main surface (panels, toolbar)
BG_SURFACE_2 = "#101A2B"     # cards, highlighted panels
BG_SURFACE_3 = "#16233A"     # elevated / active panels
BG_INNER = "#0D1726"         # recessed content (terminal/log output, code blocks)

# Borders
BORDER = "#26354B"
BORDER_HOVER = "#385891"

# Neutral (kept as an alias of TEXT_SECONDARY for readability at call sites
# that mean "neutral icon color" rather than "secondary text color")
COLOR_NEUTRAL = TEXT_SECONDARY
