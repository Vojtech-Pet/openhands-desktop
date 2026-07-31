# Changelog

Notable fixes and changes, newest first.

## 2026-07-31 — Progress-aware step limit, live reasoning preview

- The 30-step agent limit no longer cuts off genuinely productive runs -- it checks recent progress first and resets instead of stopping if the run is actually going somewhere (a 30-step, all-distinct research stretch with zero repeats was getting cut off just for being long). Only stops on real no-progress. Surfaces a visible "still making progress" notice on reset so a long collapsed stretch doesn't look stalled.
- The collapsed Working card now shows a live, LM-Studio-style reasoning preview: elapsed timer, the last two real wrapped lines of the current thinking text (updating as it streams), flipping to "Completed ✓" once the turn ends. Purely visual -- nothing is actually truncated, full text stays in the expandable body.

## 2026-07-31 — Fixed a serious bug: conversations could get permanently stuck on an unanswered question

- A conversation not attached to any open window could hang forever on `ask_user_question` with nobody able to see or answer it -- the agent would sit blocked (or get repeatedly interrupted by OpenHands's own stuck detector trying something else) with no visible sign anything needed attention.
- `ConversationWatchdog` now tries 3 escalating nudges (different tool → skip the sub-task → wrap up and finish) instead of 1 generic one before giving up, so most stuck loops resolve themselves without the user having to intervene by hand.
- `ask_user_question` auto-answers immediately in Bypass permissions mode instead of blocking forever.
- In Auto/Manual mode, the app window is forced to the front whenever a real answer is expected from the user, instead of relying on a non-modal dialog nobody notices behind other windows.
- "Continue as Code" now opens in its own new window instead of reusing the Plan window's widgets, so the Plan window can never show a stale status from a conversation that moved on.
- Log view: a floating "jump to bottom" button; auto-follow now also works for new content added inside an already-expanded Working card.

## 2026-07-31 — Auto-supervise, collapsed Working group, MCP race/freeze/keep-alive fixes

- Every new conversation is now auto-supervised by default (`ConversationWatchdog`: hash-dedup + progress scoring, nudge once on a repeated tool call, stop if it repeats again) — toggle in Settings → Agent.
- Log view: thinking, tool calls, system notices, and the benign "not a valid directory" sandbox-path-probe message all fold into one collapsed "Working" card per turn with a live status header; only the user's message and the agent's real answer/error stay always-visible.
- Fixed a startup race where AskUserServer/WorkspaceFolderServer could start accepting registration before their uvicorn socket was actually listening, permanently breaking the sandbox's MCP client for the rest of a conversation after one failed first connection.
- `connect_folder` no longer re-asks for a folder that's already connected.
- Fixed the GUI freezing for several seconds at a time: `detect_loaded_model`'s `lms ps` CLI fallback was blocking the Qt event loop directly instead of running off-thread.
- `AppServerClient` now retries once on httpx's "Server disconnected without sending a response" (a harmless uvicorn keep-alive race).
- New Mode selector (Bypass permissions/Auto/Manual) in the toolbar; Bypass also skips the workspace connect/write confirmation dialogs.
- Auto-decide Plan vs Code before starting a new conversation (toggle in Settings → LLM); a manual Plan pick always overrides it.
- LM Studio context length is now a Settings → LLM field instead of hardcoded; changing it re-syncs `max_input_tokens`/`condenser.max_tokens` proportionally.
- Live conversation-duration counter in the status bar.
- Multi-select `ask_user_question` (checkboxes) and a Mission Control delete action.

## 2026-07-31 — Multi-select ask-user, Mission Control delete, safer model unload, Continue as Code status fix

- `ask_user_question` MCP tool gains `multi_select`: checkboxes instead of buttons when more than one answer can apply at once.
- Mission Control task board gets the same per-item "Delete conversation" action the sidebar already had, wired to the real delete API.
- App shutdown no longer force-unloads all LM Studio models if another conversation is still running on the server — this was killing background conversations mid-tool-call every time the window was closed and reopened.
- "Continue as Code" now resets the status pill instead of leaving it showing the parent conversation's terminal state ("Finished (unverified)"), which looked like the new conversation was stuck even while it was actively running.

## 2026-07-31 — UI polish and connection awareness

- Removed the border/box around the "Completed" status pill (top bar and Thinking-card versions).
- Long agent/system log entries collapse behind a toggle instead of filling the screen.
- "Connected" status now actually reflects whether LM Studio's server is reachable and a model is loaded, not just whether the app-server responds. Clicking "Disconnected" starts the LM Studio server.

## 2026-07-31 — README and screenshot

- Added README with a feature overview and a clean (no private data) app screenshot.

## 2026-07-31 — Initial commit

- Native PySide6 desktop client for the OpenHands app-server: conversation log with per-tool-call cards, LLM profile management, live browser preview via noVNC, workspace file access via a confirmation-gated local MCP bridge, Supervised Agent watchdog, errors panel, Mission Control task board.
