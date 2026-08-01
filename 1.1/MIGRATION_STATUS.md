# v1.1: migration to the new OpenHands Agent Server

**Status: core golden path AND settings/profiles/skills/secrets verified
live end-to-end. Continue-as-Code and git-provider integrations still
incomplete/degraded.**

**Requires `openhands-agent-server`/`openhands-sdk`/`openhands-tools` >=
1.40.0.** Verified live against exactly 1.40.0 on 2026-08-01 -- see
`sandbox-image/Dockerfile`'s base image tag, which must stay in step with
whatever version this client is actually tested against. An older
agent-server may be missing endpoints this client calls (see the full
list in the README).

Upstream OpenHands's `origin/main` recently underwent an "Agent Canvas
migration" that deleted `openhands/app_server` entirely -- the REST backend
the [1.0](../1.0) version talks to. That backend is not coming back;
app_server is gone from upstream for good.

The replacement is `openhands-agent-server` (already the same package that
runs inside every sandbox container today) -- but running as **one
long-running server that hosts every conversation directly**, not one
container per conversation.

## Verified live, working end-to-end (2026-08-01)

Two standalone test scripts (`scripts/test_new_client.py`,
`scripts/test_controller_ws.py`) against a real running
`openhands-agent-server:1.40.0-python` container + real LM Studio:

- REST: create conversation, poll status, list events, delete conversation.
- WebSocket: `ConversationController` end-to-end -- real events streamed
  live (SystemPromptEvent -> MessageEvent -> ActionEvent/ObservationEvent
  from a real `finish()` tool call), `state_changed` correctly resolved
  RUNNING -> COMPLETED via the same `CompletionTracker`/`RunState` logic
  the shipped app uses. Not simulated -- a real local model actually
  replied and the whole pipeline (WS connect -> event batching ->
  NormalizedEvent parsing -> completion tracking) worked.

Rewritten/adapted:

- `api/client.py`: conversation create/get/search/delete, send-message,
  events/search, pause/interrupt/condense/switch_llm, desktop preview URL
  -- all against one shared base URL + `X-Session-API-Key`.
- `api/models.py`: `AppConversation` simplified (no more per-conversation
  `sandbox_status`/`conversation_url`/`session_api_key`/`sandbox_id`).
- `api/websocket_url.py`: `/sockets/events/{id}` against the shared server.
- `core/conversation_controller.py`: no more start-task polling (creation
  is synchronous now), no more separate `SandboxConversationClient`.
- `ui/main_window.py`: `start_new()` call sites fixed (no more
  `agent_type=`/`parent_conversation_id=` params); noVNC browser-preview
  section now uses the new server's own `/api/desktop/url` directly.
- `ui/task_board.py` (Mission Control): orphaned-container detection and
  "stop container via docker" removed entirely -- that failure mode
  (app-server's in-memory bookkeeping desyncing from a still-running
  Docker container) can't happen anymore; a conversation is just server
  state now, not a separate container.
- Deleted `api/sandbox_client.py` and `api/url_validation.py` (dead code,
  merged into client.py / no longer applicable).
- `main.py`: `AppServerClient` base_url/session_api_key now env-driven
  (`AGENT_SERVER_URL`, `AGENT_SERVER_SESSION_API_KEY`) instead of the old
  hardcoded port-3000 app-server.

## Verified live: settings/profiles/skills/secrets (2026-08-01)

`scripts/test_settings_client.py` against the real dev agent-server --
save/get/activate/list/delete a real LLM profile, get/update settings
(`agent_settings_diff`/`mcp_config` -- identical shape to the old
app-server's, only the HTTP method changed POST->PATCH), list skills,
create/list/delete a secret. All real round trips, not mocked.

- `settings_dialog.py`'s Agent/Condenser/Verification/Application/MCP
  pages needed NO changes -- `PATCH /api/settings` takes the exact same
  `agent_settings_diff`/`conversation_settings_diff` shape the old
  `POST /api/v1/settings` did.
- `client.py` gained real profiles/settings/skills/secrets methods,
  verified against the actual OpenAPI schema and live responses, not
  guessed.
- Secret editing is now honestly disabled in the UI (was silently broken
  otherwise): the new server's secrets endpoint is PUT-create-or-update
  with a REQUIRED value field, no metadata-only update route like the old
  one had -- editing without re-entering the value would either fail or
  blank out the real secret. Delete-and-re-add is the only path now.
- **Integrations page (git-provider tokens) is disabled**, not wired to
  anything -- checked the new server's full OpenAPI schema, there is no
  equivalent endpoint at all (unlike profiles/settings/skills/secrets,
  which all have close successors).

## NOT done yet

- **LLM credentials**: the old app-server stored API keys server-side and
  never gave them back to the client for starting a NEW conversation
  (profiles now round-trip real keys fine via `/api/profiles`, that part
  works); `_send()`'s inline conversation-start path still sends a
  hardcoded `"not-needed"` placeholder api_key instead of resolving the
  active profile's real stored key. Fine for LM Studio/local servers that
  don't check it, wrong for a real remote provider.
- **Plan/Code distinction**: the UI toggle still exists but no longer maps
  to anything server-side (the old app-server's `agent_type` concept is
  gone) -- every conversation currently gets the same default tool set.
- **Continue-as-Code**: starts a genuinely new, unrelated conversation with
  the same instructions instead of a real hand-off -- the old
  `parent_conversation_id` cross-container continuation has no equivalent
  yet. The new server's `POST /api/conversations/{id}/fork` may be the
  right primitive for a real fix.
- **Supervised Agent dialog** (`supervised_agent_dialog.py`): never passed
  an `llm_model` at all (pre-existing gap, not introduced by this
  migration) -- `start_new()` now requires one, so this dialog needs a
  model/profile picker added before it can actually run.
- **No real GUI click-through test yet** -- verified via headless scripts
  driving the same client/controller code the GUI uses, not by actually
  running the Qt app and clicking Send/opening Settings. Should still
  work (same code paths), but hasn't been watched happen in the window
  itself.

## Reference: new server, verified live 2026-08-01

```bash
docker run -d --name agent-server-dev -p 8010:8000 \
  -e OH_SESSION_API_KEYS_0=dev-key-123 \
  -e LLM_API_KEY=lm-studio -e LLM_MODEL="openai/<model>" \
  -e LLM_BASE_URL="http://172.17.0.1:1234/v1" \
  openhands-desktop/agent-server:1.40.0-python
export AGENT_SERVER_URL=http://127.0.0.1:8010
export AGENT_SERVER_SESSION_API_KEY=dev-key-123
uv run python3 -m openhands_desktop.main
```
