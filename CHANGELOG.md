# Changelog

Notable fixes and changes, newest first.

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
