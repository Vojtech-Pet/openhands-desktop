# How this app differs from stock OpenHands

*[Slovak version](DIFFERENCES.md)*

OpenHands itself (`OPENHANDS_REPO`, `app_server`) is a regular web-based
agent server — this app is an independent native desktop client talking to
it over REST/WebSocket, adding a whole layer of things that don't exist in
the original at all.

## 1. Native PySide6/Qt desktop client, not a web UI
No browser, no frontend server — a direct REST/WebSocket client to
`app_server`. Runs as a plain desktop application with its own window,
sidebar, and Mission Control panel.

## 2. Auto-supervise (ConversationWatchdog)
OpenHands has its own "stuck detector," but it's a black box with no
configurable threshold. This app watches every conversation's **live event
stream** itself and does:
- hash-based detection of repeated tool calls (same tool, same arguments)
- 3 escalating auto-nudge attempts (try a different tool → give up on the
  sub-task → summarize and continue) instead of one generic nudge
- progress-scoring: when there's real progress, it resets even the hard
  step-count ceiling (instead of a flat cutoff at 30 steps regardless of
  how productive the run actually is)
- when it genuinely gives up (and doesn't recover on its own within a few
  seconds), it **forces the window to the front** and redirects the agent
  to **propose its own concrete solutions** via `ask_user_question` --
  the app doesn't invent generic options for it

Runs automatically on every conversation (Settings → Agent → Auto-supervise
conversations).

## 3. Host filesystem access outside the sandbox (Workspace MCP bridge)
The OpenHands agent only ever sees its own sandbox (`/workspace/project`)
-- there's no API to mount an arbitrary folder on the host machine. This
app hosts its own MCP server (`connect_folder`/`list_folder`/`read_file`/
`write_file`) that solves this -- with a **mandatory user confirmation**
before every folder connection and every file write (except in Bypass
permissions mode, where it auto-approves).

## 4. Ask-user tool
OpenHands has no built-in way for the agent to ask the user for a decision
mid-task. The app adds an `ask_user_question` MCP tool with multi-select
support (checkboxes), and auto-answers it in Bypass mode instead of
blocking forever.

## 5. Mode selector (Bypass / Auto / Manual)
One combo box in the toolbar that sets `confirmation_mode` +
`security_analyzer` at once (otherwise scattered across Settings →
Verification), and also controls whether the app autonomously approves
workspace/ask-user requests.

## 6. Auto-decide Plan vs Code
Before starting a new conversation, the app makes one cheap LLM call to
decide whether the task needs a plan first or can go straight to code --
instead of you picking manually every time. A manual pick always wins.

## 7. Continue as Code (Plan → Act handoff)
When a Plan conversation finishes, the app offers (or automatically
triggers) continuing as a Code agent, linked via `parent_conversation_id`
-- something that doesn't exist in the original as a standalone,
one-click workflow.

## 8. Collapsed "Working" log with a live preview
Instead of every reasoning step and tool call showing up as its own card
(or a chat UI just dumping raw text), the app folds a whole turn into one
collapsed card with an LM-Studio-style live preview (the last 2 lines of
the current reasoning text, an elapsed timer, "Completed ✓" once done) --
you can tell something is happening without expanding anything.

## 9. Deeper LM Studio integration
- Automatic detection and switching of the loaded model per profile
- Configurable context length (Settings → LLM) that, when changed,
  automatically recalculates and syncs `max_input_tokens`/
  `condenser.max_tokens` on the profiles (instead of manual tuning)
- Recognizes when LM Studio is unreachable/has no model loaded, and
  starts the server with one click from the app

## 10. Mission Control + Errors panel
An overview of every conversation on the server (not just the open one),
with live status and delete support -- plus a dedicated panel collecting
every error from the current conversation in one place.

## 11. Explicit Plan/Code instructions instead of trial and error
Confirmed live: a Plan agent, with no explanation, repeatedly tried
`invoke_skill("ssh")` hoping it would somehow reach the internet, before
eventually reasoning out on its own that it was a Plan agent with no
terminal. The app now tells every agent, right at the start, exactly what
its job is and what tools it does/doesn't have (Plan: glob/grep/planning
editor, no terminal; Code: terminal/file editor/git, actually verify the
plan's steps instead of assuming they were right) -- instead of leaving it
to figure that out mid-task.

## 12. Robustness OpenHands' own web UI doesn't handle
- retries a known, harmless httpx/uvicorn keep-alive race
- guards against MCP servers that start registering before they're
  actually listening (permanently breaks the MCP session for the rest of
  a conversation otherwise)
- doesn't unload LM Studio's models on window close if another
  conversation is still running somewhere
- fixed the whole GUI freezing for several seconds at a time (a
  synchronous `lms ps` fallback was running directly on the Qt event loop)
