"""REST client for the new (Agent Canvas-era) OpenHands Agent Server: one
long-running server instance hosting every conversation directly, verified
live against a real running agent-server 1.40.0 on 2026-08-01 (see
`/tmp/agent-server-openapi.json` captured from that run, and
openhands.agent_server's own router source for the authoritative
definitions).

Replaces the old two-tier split entirely:
- AppServerClient (port 3000, orchestrated N per-conversation sandbox
  containers, async start-task polling) is gone -- POST /api/conversations
  now creates and returns a conversation synchronously, no polling.
- SandboxConversationClient (talked to one conversation's own container at
  its own conversation_url/session_api_key) is gone too -- interrupt/pause/
  condense/switch_llm are now just more paths on this same client, against
  this same server.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from openhands_desktop.api.models import AppConversation, LlmProfile

DEFAULT_WORKSPACE_PATH = "workspace/project"
DEFAULT_SETTINGS_CACHE_PATH = Path.home() / ".config" / "openhands-desktop" / "settings-cache.json"


class AppServerClient:
    """Name kept as `AppServerClient` (not renamed to e.g. AgentServerClient)
    to minimize churn in the UI layer, which was written against the old
    class -- it now talks to the Agent Server instead, everywhere."""

    def __init__(self, base_url: str = "http://127.0.0.1:8010", session_api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_api_key = session_api_key
        headers = {"X-Session-API-Key": session_api_key} if session_api_key else {}
        # retries=1: same rationale as the old client -- a pooled keep-alive
        # connection closed server-side right as it's reused shouldn't be a
        # user-visible failure for an idempotent GET/POST.
        transport = httpx.AsyncHTTPTransport(retries=1)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=30.0, transport=transport, headers=headers
        )
        self._settings_cache_path = DEFAULT_SETTINGS_CACHE_PATH

    def _load_cached_settings(self) -> dict:
        try:
            data = self._settings_cache_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return {}
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}

    def _save_cached_settings(self, settings: dict) -> None:
        try:
            self._settings_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_cache_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            pass

    async def _request_json_with_cache(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = await getattr(self._client, method)(path, **kwargs)
            resp.raise_for_status()
            data = resp.json()
            self._save_cached_settings(data)
            return data
        except (httpx.HTTPError, httpx.TimeoutException, httpx.ConnectError):
            return self._load_cached_settings()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200 and resp.json().get("status") == "ok"
        except httpx.HTTPError:
            return False

    # -- Conversation lifecycle -------------------------------------------

    async def start_conversation(
        self,
        *,
        llm_model: str,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        initial_message_text: str | None = None,
        tools: list[str] | None = None,
        agent_profile: str | None = None,
        system_message_suffix: str | None = None,
        mcp_config: dict | None = None,
    ) -> AppConversation:
        """Synchronous now, unlike the old app-server: the response IS the
        finished conversation, not a task id to poll (verified live --
        POST /api/conversations returns the full AppConversationInfo-
        shaped object immediately, execution_status already resolved).

        `agent_profile` (an `agent-profiles` name, e.g. our
        "project-module-engineer" subagent) is not yet wired up here --
        this app doesn't manage agent-profiles server-side yet, only the
        default inline `agent.llm`/`agent.tools` shape.

        `mcp_config`: unlike the old app-server, a new conversation here
        does NOT inherit the global /api/settings agent_settings.mcp_config
        automatically -- confirmed live 2026-08-01, a conversation started
        right after registering this app's ask-user/workspace-bridge MCP
        servers in global settings still came back with agent.mcp_config
        == {} and neither tool available, sending a real conversation into
        a host-path-confusion loop with no way to actually connect the
        folder it needed. Callers must pass the current mcp_config
        explicitly per conversation.
        """
        agent: dict = {
            "llm": {
                "model": llm_model,
                "usage_id": "agent",
            },
            "tools": [{"name": name} for name in (tools or ["terminal", "file_editor", "task_tracker", "browser_tool_set"])],
            "kind": "Agent",
        }
        if llm_base_url:
            agent["llm"]["base_url"] = llm_base_url
        if llm_api_key:
            agent["llm"]["api_key"] = llm_api_key
        if system_message_suffix:
            agent["system_prompt_kwargs"] = {"system_message_suffix": system_message_suffix}
        if mcp_config:
            agent["mcp_config"] = mcp_config
        payload: dict = {
            "workspace": {"working_dir": DEFAULT_WORKSPACE_PATH, "kind": "LocalWorkspace"},
            "agent": agent,
        }
        if initial_message_text:
            payload["initial_message"] = {"content": [{"text": initial_message_text}]}
        resp = await self._client.post("/api/conversations", json=payload)
        resp.raise_for_status()
        return AppConversation.from_json(resp.json())

    async def get_conversation(self, conversation_id: str) -> AppConversation:
        resp = await self._client.get(f"/api/conversations/{conversation_id}")
        resp.raise_for_status()
        return AppConversation.from_json(resp.json())

    async def search_conversations(self, limit: int = 50) -> list[AppConversation]:
        resp = await self._client.get("/api/conversations/search", params={"limit": limit})
        resp.raise_for_status()
        return [AppConversation.from_json(item) for item in resp.json().get("items", [])]

    async def delete_conversation(self, conversation_id: str) -> None:
        resp = await self._client.delete(f"/api/conversations/{conversation_id}")
        if resp.status_code == 404:
            return  # already gone -- goal state achieved, not a failure (see openhands-desktop's client.py precedent)
        resp.raise_for_status()

    async def send_message(self, conversation_id: str, text: str, *, run: bool = True) -> None:
        resp = await self._client.post(
            f"/api/conversations/{conversation_id}/events",
            json={"role": "user", "content": [{"text": text}], "run": run},
        )
        resp.raise_for_status()

    async def search_events(
        self, conversation_id: str, *, limit: int = 100, page_id: str | None = None
    ) -> dict:
        params: dict = {"limit": limit}
        if page_id:
            params["page_id"] = page_id
        resp = await self._client.get(f"/api/conversations/{conversation_id}/events/search", params=params)
        resp.raise_for_status()
        return resp.json()

    # -- Run control (used to live in SandboxConversationClient, one per
    # conversation container -- now just more paths on this one client) ---

    async def pause(self, conversation_id: str) -> None:
        resp = await self._client.post(f"/api/conversations/{conversation_id}/pause")
        resp.raise_for_status()

    async def interrupt(self, conversation_id: str) -> None:
        resp = await self._client.post(f"/api/conversations/{conversation_id}/interrupt")
        resp.raise_for_status()

    async def condense(self, conversation_id: str) -> None:
        resp = await self._client.post(f"/api/conversations/{conversation_id}/condense")
        resp.raise_for_status()

    async def switch_llm(self, conversation_id: str, llm_config: dict) -> None:
        resp = await self._client.post(
            f"/api/conversations/{conversation_id}/switch_llm", json={"llm": llm_config}
        )
        resp.raise_for_status()

    # -- LLM profiles (server-side /api/profiles -- present on the new
    # server too, not yet wired up beyond this passthrough) ---------------

    async def get_desktop_url(self) -> str | None:
        """The new server exposes its own /api/desktop/url directly --
        replaces the old per-sandbox noVNC-exposed-port lookup entirely
        (verified live in the OpenAPI schema: GET /api/desktop/url,
        GET /api/vscode/url). Desktop must be enabled server-side
        (OH_ENABLE_DESKTOP=1 or similar) or this returns None/404."""
        resp = await self._client.get("/api/desktop/url")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("url")

    # -- LLM profiles, settings, skills -- verified live 2026-08-01 against
    # agent-server 1.40.0's real request/response bodies, structurally very
    # close to the old app-server's own shapes (this server was clearly
    # designed as its logical successor for these endpoints specifically,
    # unlike the conversation-lifecycle ones which changed completely). --

    async def list_llm_profiles(self) -> tuple[list[LlmProfile], str | None]:
        resp = await self._client.get("/api/profiles")
        resp.raise_for_status()
        data = resp.json()
        profiles = [LlmProfile.from_json(p) for p in data.get("profiles", [])]
        return profiles, data.get("active_profile")

    async def save_profile(
        self,
        name: str,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        preserve_existing_api_key: bool = False,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_effort: str | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        stream: bool | None = None,
        caching_prompt: bool | None = None,
        native_tool_calling: bool | None = None,
        enable_encrypted_reasoning: bool | None = None,
        prompt_cache_retention: str | None = None,
        extended_thinking_budget: int | None = None,
        litellm_extra_body: dict | None = None,
        extra_config: dict | None = None,
    ) -> None:
        """Kept the old app-server client's rich signature (same kwargs,
        same dict-assembly logic) rather than changing every call site --
        only the wire format at the end changed: POST /api/profiles/{name}
        with {"llm": ..., "include_secrets": ...} (verified live), not the
        old {"llm": ..., "preserve_existing_api_key": ...}. Semantics also
        flipped: `include_secrets=True` means "persist the api_key value
        with this profile" (default), which is the closest equivalent to
        the old "don't preserve [drop] the existing key" default of False
        -- both boil down to "send whatever key we actually have."
        """
        llm: dict = dict(extra_config or {})
        llm.pop("api_key", None)
        llm["model"] = model
        if base_url:
            llm["base_url"] = base_url
        else:
            llm.pop("base_url", None)
        if api_key:
            llm["api_key"] = api_key
        if temperature is not None:
            llm["temperature"] = temperature
        if top_p is not None:
            llm["top_p"] = top_p
        if top_k is not None:
            llm["top_k"] = top_k
        if reasoning_effort:
            llm["reasoning_effort"] = reasoning_effort
        if max_input_tokens is not None:
            llm["max_input_tokens"] = max_input_tokens
        if max_output_tokens is not None:
            llm["max_output_tokens"] = max_output_tokens
        if stream is not None:
            llm["stream"] = stream
        if caching_prompt is not None:
            llm["caching_prompt"] = caching_prompt
        if native_tool_calling is not None:
            llm["native_tool_calling"] = native_tool_calling
        if enable_encrypted_reasoning is not None:
            llm["enable_encrypted_reasoning"] = enable_encrypted_reasoning
        if prompt_cache_retention is not None:
            llm["prompt_cache_retention"] = prompt_cache_retention
        if extended_thinking_budget is not None:
            llm["extended_thinking_budget"] = extended_thinking_budget
        if litellm_extra_body is not None:
            llm["litellm_extra_body"] = litellm_extra_body
        payload = {"llm": llm, "include_secrets": not preserve_existing_api_key or bool(api_key)}
        resp = await self._client.post(f"/api/profiles/{name}", json=payload)
        resp.raise_for_status()

    async def get_profile_detail(self, name: str) -> dict:
        """Returns the raw {"name", "config"} response -- unlike the old
        app-server, the LLM config is nested under "config" here (verified
        live), not flat. Callers that already did `data.get("config")` on
        the old (already-flat, so this was a no-op there) response keep
        working unchanged."""
        resp = await self._client.get(f"/api/profiles/{name}")
        resp.raise_for_status()
        return resp.json()

    async def delete_profile(self, name: str) -> None:
        resp = await self._client.delete(f"/api/profiles/{name}")
        resp.raise_for_status()

    async def activate_profile(self, name: str) -> None:
        resp = await self._client.post(f"/api/profiles/{name}/activate")
        resp.raise_for_status()

    async def get_settings(self) -> dict:
        """Raw settings blob -- verified live to still have the
        `agent_settings` top-level key the old app-server's did, though the
        nested shape has moved on (schema_version 5, agent_kind, etc).
        Falls back to the last locally cached snapshot when offline, same
        as before."""
        return await self._request_json_with_cache("get", "/api/settings")

    async def update_settings(self, payload: dict) -> None:
        """PATCH, not POST -- the new server uses PATCH /api/settings for a
        partial update (verified live in the OpenAPI schema), where the old
        app-server used POST /api/v1/settings for the same semantics."""
        try:
            resp = await self._client.patch("/api/settings", json=payload)
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException, httpx.ConnectError):
            cached = self._load_cached_settings()
            if not isinstance(cached, dict):
                cached = {}
            merged = dict(cached)
            for key, value in payload.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            self._save_cached_settings(merged)
            return
        cached = self._load_cached_settings()
        if isinstance(cached, dict):
            merged = dict(cached)
            for key, value in payload.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            self._save_cached_settings(merged)

    async def list_skills(self) -> list[dict]:
        """/api/skills/search doesn't exist on the new server -- the closest
        equivalent is /api/skills/installed (verified live), which is not
        quite the same thing (installed vs. searchable/available), but is
        what's actually wired up server-side for this app to read."""
        resp = await self._client.get("/api/skills/installed")
        resp.raise_for_status()
        return resp.json().get("skills", [])

    async def list_secrets(self) -> list[dict]:
        """/api/settings/secrets (verified live), not /api/v1/secrets like
        the old app-server -- same PUT-to-create-or-update, single-item-
        delete semantics though, so this is otherwise a straight port."""
        resp = await self._client.get("/api/settings/secrets")
        resp.raise_for_status()
        return resp.json().get("secrets", [])

    async def create_secret(self, name: str, value: str, description: str | None = None) -> None:
        resp = await self._client.put(
            "/api/settings/secrets", json={"name": name, "value": value, "description": description}
        )
        resp.raise_for_status()

    async def update_secret(self, name: str, value: str, description: str | None = None) -> None:
        """Same PUT endpoint as create -- it's create-or-update by name
        (verified live), there's no separate update-by-id route like the
        old app-server's PUT /api/v1/secrets/{secret_id} (secrets here are
        keyed by name directly, not a separate opaque id)."""
        await self.create_secret(name, value, description)

    async def delete_secret(self, name: str) -> None:
        resp = await self._client.delete(f"/api/settings/secrets/{name}")
        resp.raise_for_status()

    # -- No equivalent found on the new server (checked its full OpenAPI
    # schema -- not present under any path): git-provider OAuth token
    # storage (the old app-server's /api/v1/secrets/git-providers, which
    # validated tokens against the real provider API before storing them).
    # The Integrations settings page should show "not available yet"
    # rather than call something that doesn't exist. See
    # MIGRATION_STATUS.md.
