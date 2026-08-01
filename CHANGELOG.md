# Changelog

Notable fixes and changes, newest first.

## 2026-08-01 — Sandbox image now also has httpx/aiohttp (async HTTP)

- `requests` only covers sync HTTP; async Python code needs `httpx` or `aiohttp` instead, and neither ships in the stock OpenHands image (verified directly against `ghcr.io/openhands/agent-server:1.37.1-python`, not just ours). Both are now installed.

## 2026-08-01 — Sandbox image now has requests/beautifulsoup4/yt-dlp/ffmpeg/file/7z/exiftool + self-install fallback

- The project-module-engineer subagent's own playbook assumes several tools are available, but none of them were actually in the base agent-server image -- every real task hit `ModuleNotFoundError: No module named 'requests'` on its first HTTP call. Verified directly against the freshly built image that all seven are now present. Only affects new conversations (new sandbox containers) -- any already-running conversation is still on the old image until it's restarted.
- Also documented a fallback in the subagent's own instructions: if it hits a missing tool this image didn't anticipate, self-install via pip/`sudo apt-get` (verified this sandbox user has working passwordless sudo) instead of treating it as a dead end.

## 2026-08-01 — Detect and recover from silent conversation hangs (empty LLM response)

- Confirmed live: an OpenHands SDK warning ("LLM response contained no tool call and no content") was followed by total silence forever -- `execution_status` stayed "running" (nothing for the status poll to correct) and the watchdog only ever compares ActionEvents, so with literally nothing happening it never fires. New periodic check: if a conversation shows RUNNING but no event arrived in 90s, interrupt and redirect it, and raise the window -- this could otherwise sit silently stuck for hours with zero visible sign.

## 2026-08-01 — Correct host-path-vs-sandbox confusion after the first failure, not the third

- A proper fix would validate path scope in the tool's own JSON schema before the call ever reaches the sandbox, but glob/grep/file_editor are stock OpenHands SDK tools, not something this app can patch. Instead, the app now watches every failed glob/grep/file_editor observation client-side: if the error says "not a valid directory" and contains a host-looking path prefix, it queues a targeted correction after the very first occurrence -- faster than relying on the generic 3-strikes repeat detector to eventually catch the same pattern.

## 2026-08-01 — Always auto-scroll; notify on STUCK; fix glob-vs-host-path confusion

- Log view now always follows new content to the bottom (reversing the previous "only if already at bottom" behavior), across new rows, an expanded Working card, and a growing reasoning row.
- Auto-supervise's STUCK event appended a visible row but never actually notified anyone -- confirmed live a conversation sat paused for hours with zero visible sign. Now flashes/raises the window like a real error.
- Root-caused a real stuck loop: the agent correctly used `workspace_list_folder` on a host path the user gave it, then later switched to plain `glob` on that same host path (which only sees `/workspace/project`), got "not a valid directory" every time, and looped until stopped. Added an explicit instruction distinguishing host-path tools from sandbox-only tools.

## 2026-08-01 — Fixed garbled reasoning text; Agent rows changed from green to purple

- Each streaming reasoning chunk was being `.strip()`'d before appending -- confirmed live the leading/trailing space of a chunk is often the real word-separator once concatenated ("Teraz sa" rendering as "Terazsa"). Fixed.
- Agent-styled rows (now used for reasoning/narration too, not just final replies) were green, which reads as "completed" elsewhere in the app -- changed to purple.

## 2026-08-01 — Reasoning/thinking is its own visible row again, not folded into Working

- Reversal of the "fold thinking into the collapsed Working card" design from the day before: confirmed live via screenshot that the model's reasoning channel is where the app's own narration instructions ("Looking for X", "Found it: Y") actually land, so collapsing it made exactly that narration easy to miss. Reasoning now renders as its own always-visible row, styled like an agent reply, growing in place as it streams. Tool calls/results and system notices still fold into Working as before -- only "thinking" moved.

## 2026-08-01 — Flash the taskbar icon when a background window's agent replies

- With two windows open, a real agent reply in the window you're not looking at was easy to miss entirely. A lighter, non-intrusive notification (taskbar/dock flash only, no stolen focus) now fires whenever a genuine agent message or finish() lands in a window that isn't currently active.

## 2026-08-01 — Mission Control detects orphaned sandbox containers

- An app-server restart mid-conversation loses its (in-memory-only) sandbox bookkeeping, but the actual Docker container keeps running -- `docker ps` sees it fine while the search API reports `sandbox_status: "MISSING"` or omits the conversation entirely, so nothing in the API can reach or stop it, and the app previously had no way to even know it existed. Mission Control now cross-checks `docker ps` against every conversation's reported sandbox on each reload, surfaces any unmatched container as "orphaned", and offers a direct Stop-container action.

## 2026-07-31 — Fixed Plan getting stuck trying to delegate (it has no launch_subagent tool)

- A mandatory sub-agent delegation instruction (for a user-specific project) had been added to the shared base suffix, which Plan also receives -- Plan has no `launch_subagent` tool at all (confirmed live), so it got stuck searching the filesystem trying to "find" the subagent and hit the STUCK detector. Moved the delegation instruction into Code-only instructions; Plan now knows it doesn't have delegation and writes a note into PLAN.md for Code to delegate instead.

## 2026-07-31 — Second independent window; mandatory sub-agent delegation example

- New "New window" action (composer's "⋯" menu): opens a second, fully independent window sharing this process's client/MCP servers, so you can keep working (or just watch) while the first window's conversation is busy. Shutdown is now aware of how many windows are open -- only the last one closing actually tears down the shared servers and checks whether it's safe to unload the model.
- Added an example project-specific sub-agent (site-specific extraction playbook, known site patterns, the module contract) and made delegation to it mandatory for its category of task, overriding the general "use subagents only when it genuinely helps" guidance.

## 2026-07-31 — Warn when sending a chat message to a busy conversation

- `send-message` only appends to the conversation's event history -- it does not interrupt an in-flight LLM call, and whether the agent even acts on a new message depends entirely on the model noticing and prioritizing it over what it was already doing. Verified live: a message telling a running agent to "stop, wait for instructions" was recorded in ~0.15s but had zero effect -- the agent kept executing its original plan through several more steps. Sending a message while the conversation is running now shows a note that it's queued, not acted on immediately, and to use Stop first for an actual immediate reaction.

## 2026-07-31 — Reset log/Errors count when the active conversation is deleted from Mission Control

- Deleting the currently-shown conversation via Mission Control (instead of the sidebar) left the window displaying a stale log/Errors count for a conversation that no longer existed server-side -- Mission Control now tells the main window when this happens, which resets itself and drops matching local sidebar entries.

## 2026-07-31 — Fixed status pill getting permanently stuck on "Running…"

- The grace-window fix from earlier today (correct a stale ERROR/Waiting poll reading within 8s of real activity) could leave the status pill stuck showing "Running…" forever for a conversation that had genuinely finished, because the controller's status-poll loop only re-emits `state_changed` on an actual value change and had no way to know the override happened in the view layer. Confirmed live with a screenshot: "Running… 14m 27s" long after the agent's own completion message. Fixed with a generation-guarded scheduled re-check exactly when the grace window elapses.

## 2026-07-31 — Family-aware delete actually works now (sub_conversation_ids was a dead end)

- The family-aware delete fix from earlier today read `sub_conversation_ids` to find a conversation's Continue-as-Code children -- end-to-end testing (a real Plan+Code pair created via the API, then actually run through the delete path) showed that field comes back empty on both the list and single-conversation endpoints, with or without `include_sub_conversations=true`, even on a parent with a real running child. It was silently a no-op beyond the one id already known.
- `get_conversation()` was also missing `include_sub_conversations=true` (only `search_conversations()` had it) -- without it, `parent_conversation_id` itself comes back `null` even for a genuine child.
- New `resolve_conversation_family()` walks the real graph via `parent_conversation_id` only (up to the root, then back down by scanning for matches) -- verified directly against a real API-created Plan+Code pair that it finds the correct family from either id, and that deleting it actually removes the shared Docker container afterward.

## 2026-07-31 — Continue-as-Code status/sidebar fixes, family-aware delete, live LLM toggle push

- `search_conversations()` was missing `include_sub_conversations=true` -- Continue-as-Code children were completely invisible to Mission Control and to the "is anything else running" safety check used before unloading the model.
- Deleting a conversation now resolves and deletes its whole Plan/Code family (parent + sub-conversations), not just the one id clicked -- a Continue-as-Code pair shares one sandbox container server-side, which the server only tears down once every conversation referencing it is gone (confirmed directly in OpenHands' own `app_conversation_router.py`, `_finalize_sandbox_delete`: "delete the sandbox if unreferenced"). Applies to both the sidebar's per-conversation delete and Mission Control's per-task/"Delete all" actions.
- Continue-as-Code swaps the Plan sidebar entry's id in place instead of inserting a second row for the same task; the old Plan controller is disconnected before the model-switch wait instead of after, so its status polling can't overwrite a fresh "running" write.
- Sidebar status writes are now serialized so out-of-order DB writes can't leave a stale status on screen.
- New "Continued as Code" status (from the server's own `sub_conversation_ids`) replaces a misleading plain "Finished" after reattaching to an already-continued Plan conversation.
- `FINISHED_UNVERIFIED` relabeled "Waiting" (was "Finished (unverified)").
- Fixed a real status-flicker/frozen-timer bug: a status poll and a live event race in either order, so a stale "finished"/"error" poll reading could flip the label back after a correct update. Any real event within 8s of "now" now overrides a stale terminal reading regardless of arrival order.
- Continue-as-Code's initial message now explicitly tells the Code agent to read `.agents_tmp/PLAN.md` first and reconnect the real host folder via `workspace_connect_folder` (its sandbox doesn't inherit the Plan sandbox's connection) -- this was the actual cause of a Code agent wandering into curl/browser exploration instead of implementing anything.
- Code-mode instructions gained an explicit override for the SDK's built-in "propose a new plan" troubleshooting guidance; the plan-continuation-specific note is now only appended when the conversation was actually continued from a Plan.
- Plan-mode instructions rewritten much more strictly after the softer version still let an agent behave as if it had a terminal.
- New instruction: always call `finish` explicitly as the last action.
- Switching Thinking/Keep for a profile driving a running conversation now applies immediately via the sandbox's own `switch_llm` endpoint, instead of only affecting the next new conversation.
- New "Unload" button next to the model chip to free VRAM on demand.
- Mission Control gained a "Delete all" action with visible progress and a failure tally.

## 2026-07-31 — DIFFERENCES.md, single-instance guard, Plan/Code role instructions

- Added `DIFFERENCES.md`/`DIFFERENCES.en.md` documenting what this app adds on top of stock OpenHands.
- Single-instance guard: launching the app a second time now just raises the already-running window instead of starting a broken second process that fights the first over the fixed-port MCP servers.
- Reverted "Continue as Code opens a second window" -- caused a real bug (closing the Plan window while a secondary was mid-setup unloaded the model out from under it), and a second window wasn't wanted anyway. Back to reusing the same window.
- Plan and Code agents each get an explicit note at conversation start about their actual role and available/unavailable tools, so they stop discovering their own limits by trial and error (confirmed live: a Plan agent tried `invoke_skill("ssh")` six times hoping to reach the network before it had no terminal at all).
- Auto-supervise's stuck recovery waits ~6s to see if the conversation recovers on its own before doing anything (a race with an in-flight nudge made it fire pointlessly before), then redirects the agent to propose its own concrete next steps via `ask_user_question` instead of the app guessing generic options.
- Agent custom instructions gained sections on not retrying tool/skill calls that only returned static docs, and asking via `ask_user_question` when a decision is genuinely ambiguous.

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
