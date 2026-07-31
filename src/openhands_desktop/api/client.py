"""REST client for the OpenHands app-server, using only endpoints verified
live against a running server on 2026-07-27 (see app_conversation_router.py /
status_router.py in the OpenHands source for the authoritative definitions).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from openhands_desktop.api.models import AppConversation, AppConversationStartTask, LlmProfile

# Confirmed live 2026-07-28: the sandbox's repo root for a fresh conversation
# with no `selected_repository` given. The API has no per-conversation way to
# ask for this path directly, so it's a convention, not a guarantee.
DEFAULT_WORKSPACE_PATH = "/workspace/project"
DEFAULT_SETTINGS_CACHE_PATH = Path.home() / ".config" / "openhands-desktop" / "settings-cache.json"


class AppServerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:3000") -> None:
        self.base_url = base_url.rstrip("/")
        # retries=1 covers a specific, harmless race: uvicorn closes an idle
        # keep-alive connection after its own timeout, and if httpx reuses
        # that exact pooled connection just as/after that happens, the
        # request fails with "Server disconnected without sending a
        # response." -- confirmed live 2026-07-31 via the status-polling
        # loop's error_occurred signal. httpx's own documented fix: retry
        # once on a fresh connection (safe here, every call this client
        # makes is either GET or an idempotent-in-practice POST/DELETE that
        # never partially applied on the failed attempt, since it never
        # reached the server).
        transport = httpx.AsyncHTTPTransport(retries=1)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0, transport=transport)
        self._settings_cache_path = DEFAULT_SETTINGS_CACHE_PATH

    def _load_cached_settings(self) -> dict:
        try:
            data = self._settings_cache_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
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

    async def _request_json(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = await getattr(self._client, method)(path, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.TimeoutException, httpx.ConnectError):
            cached = self._load_cached_settings()
            if cached:
                return cached
            raise

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

    async def list_llm_profiles(self) -> tuple[list[LlmProfile], str | None]:
        resp = await self._client.get("/api/v1/settings/profiles")
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
        """Creates the profile if `name` is new, or overwrites it if it
        already exists -- same endpoint covers both Add and Edit (confirmed
        live: POST /api/v1/settings/profiles/{name} returns 201 either way).

        `temperature`/`top_p`/`top_k` are real top-level StrictLLM fields
        (confirmed live via GET /api/v1/settings/profiles/{name}); min_p,
        presence_penalty, repetition_penalty, and chat_template_kwargs
        (preserve_thinking) are NOT top-level fields on this model -- they
        only exist inside the free-form `litellm_extra_body` dict.
        """
        # The profile endpoint replaces the complete LLM object. Start from
        # the fetched config when editing so controls exposed by another
        # client/version are not silently discarded.
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
        payload = {"llm": llm, "preserve_existing_api_key": preserve_existing_api_key}
        resp = await self._client.post(f"/api/v1/settings/profiles/{name}", json=payload)
        resp.raise_for_status()

    async def get_profile_detail(self, name: str) -> dict:
        """Full LLM config for one profile (api_key nulled out, api_key_set
        reports whether one is stored) -- used to pre-fill the Edit form
        with the profile's *current* sampling settings, which the list
        endpoint doesn't include."""
        resp = await self._client.get(f"/api/v1/settings/profiles/{name}")
        resp.raise_for_status()
        return resp.json()

    async def delete_profile(self, name: str) -> None:
        resp = await self._client.delete(f"/api/v1/settings/profiles/{name}")
        resp.raise_for_status()

    async def activate_profile(self, name: str) -> None:
        """Server-side activation: switches `agent_settings.llm` so this
        profile becomes the account-wide default, not just this client's
        toolbar selection."""
        resp = await self._client.post(f"/api/v1/settings/profiles/{name}/activate")
        resp.raise_for_status()

    async def get_settings(self) -> dict:
        """Returns the raw settings JSON (confirmed live 2026-07-28): a large,
        mostly-flat blob with `agent_settings` (agent, condenser, mcp_config,
        agent_context, ...) and `conversation_settings` (confirmation_mode,
        security_analyzer) nested inside. Kept as a raw dict rather than a
        fully typed model -- the surface is large and most of it isn't used
        here; callers pick out the fields they need. Falls back to the last
        locally cached snapshot when the server is unavailable."""
        return await self._request_json_with_cache("get", "/api/v1/settings")

    async def update_settings(self, payload: dict) -> None:
        """POST /api/v1/settings deep-merges `agent_settings_diff` and
        `conversation_settings_diff` with existing values (confirmed live)
        -- a partial diff is safe and won't clobber sibling fields, with one
        exception: `agent_settings_diff.mcp_config` is validated/replaced as
        a whole unit, not merged per-server-entry (confirmed live: adding one
        new MCP server via a partial diff silently wiped out the others) --
        callers touching mcp_config must always send the complete resulting
        dict, never a partial one. When the server is offline, the update is
        still persisted into the local settings cache so the UI stays usable."""
        try:
            resp = await self._client.post("/api/v1/settings", json=payload)
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
        resp = await self._client.get("/api/v1/skills/search", params={"limit": 100})
        resp.raise_for_status()
        return resp.json().get("items", [])

    async def list_secrets(self) -> list[dict]:
        resp = await self._client.get("/api/v1/secrets/search", params={"limit": 100})
        resp.raise_for_status()
        return resp.json().get("items", [])

    async def create_secret(self, name: str, value: str, description: str | None = None) -> None:
        resp = await self._client.post(
            "/api/v1/secrets", json={"name": name, "value": value, "description": description}
        )
        resp.raise_for_status()

    async def update_secret(self, secret_id: str, name: str, description: str | None = None) -> None:
        resp = await self._client.put(
            f"/api/v1/secrets/{secret_id}", json={"name": name, "description": description}
        )
        resp.raise_for_status()

    async def delete_secret(self, secret_id: str) -> None:
        resp = await self._client.delete(f"/api/v1/secrets/{secret_id}")
        resp.raise_for_status()

    async def store_git_provider_token(self, provider: str, token: str) -> None:
        """The server validates the token against the real provider API
        before storing it (confirmed live: a fake token returns 401
        "Invalid token"). Sends only the one provider being connected --
        merge-vs-replace semantics for sibling providers aren't confirmed
        against a populated account, so callers should warn accordingly."""
        resp = await self._client.post(
            "/api/v1/secrets/git-providers",
            json={"provider_tokens": {provider: {"token": token}}},
        )
        resp.raise_for_status()

    async def unset_git_provider_tokens(self) -> None:
        """Unsets ALL configured git provider tokens at once -- there is no
        per-provider disconnect endpoint (confirmed live)."""
        resp = await self._client.delete("/api/v1/secrets/git-providers")
        resp.raise_for_status()

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200 and resp.json() == "OK"
        except httpx.HTTPError:
            return False

    async def start_conversation(
        self,
        *,
        llm_model: str | None = None,
        selected_repository: str | None = None,
        initial_message_text: str | None = None,
        agent_type: str | None = None,
        parent_conversation_id: str | None = None,
        system_message_suffix: str | None = None,
    ) -> AppConversationStartTask:
        """`agent_type`: "default" (terminal, file_editor, ... -- full
        read/write agent) or "plan" (glob, grep, planning_file_editor,
        finish, think only -- no terminal, read-only). Confirmed directly
        in AppConversationStartRequest / AgentType in the OpenHands source,
        not assumed.

        `system_message_suffix` is the ONLY working channel for custom
        agent instructions in this deployment. Setting
        `agent_settings.agent_context.system_message_suffix` looks like it
        should work -- it saves, reads back, and renders in the settings UI
        -- but has no effect: the app-server discards the stored
        agent_context and rebuilds it from the *start request* instead
        (live_status_app_conversation_service.py builds
        `AgentContext(system_message_suffix=effective_suffix, secrets=...)`
        from `request.system_message_suffix`). Verified live 2026-07-29 on
        fresh conversations: text saved in settings appears in neither the
        system prompt nor the dynamic context, while text passed here does.
        The same overwrite silently drops `agent_context.skills` and
        `load_user_skills`, which is why user skills never reach the agent.
        """
        payload: dict = {}
        if llm_model:
            payload["llm_model"] = llm_model
        if selected_repository:
            payload["selected_repository"] = selected_repository
        if agent_type:
            payload["agent_type"] = agent_type
        if parent_conversation_id:
            payload["parent_conversation_id"] = parent_conversation_id
        if system_message_suffix:
            payload["system_message_suffix"] = system_message_suffix
        if initial_message_text:
            payload["initial_message"] = {
                "role": "user",
                "content": [{"type": "text", "text": initial_message_text}],
            }
        resp = await self._client.post("/api/v1/app-conversations", json=payload)
        resp.raise_for_status()
        return AppConversationStartTask.from_json(resp.json())

    async def get_start_task(self, task_id: str) -> AppConversationStartTask:
        resp = await self._client.get(
            "/api/v1/app-conversations/start-tasks/search", params={"ids": task_id}
        )
        resp.raise_for_status()
        items = resp.json()["items"]
        if not items:
            raise LookupError(f"start task {task_id} not found")
        return AppConversationStartTask.from_json(items[0])

    async def get_conversation(self, conversation_id: str) -> AppConversation:
        """Uses /search?ids=... instead of the direct GET
        /api/v1/app-conversations/{id} route -- confirmed live 2026-07-29
        against the current docker.openhands.dev/openhands/openhands:latest
        image that the direct route returns 200 with the frontend's HTML
        shell (a React Router SPA fallback winning over the API route),
        not JSON, which crashed every caller with a JSONDecodeError
        ("Expecting value: line 1 column 1"). /search correctly returns
        application/json either way, so it's used as the single-conversation
        lookup unconditionally rather than only as a fallback.
        """
        resp = await self._client.get("/api/v1/app-conversations/search", params={"ids": conversation_id})
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            raise LookupError(f"conversation {conversation_id} not found")
        return AppConversation.from_json(items[0])

    async def get_novnc_url(self, sandbox_id: str) -> str | None:
        """Web URL for the sandbox's noVNC browser preview (see docker_sandbox_service.py's
        NOVNC exposed port, 2026-07-30) -- only present when the sandbox image was
        started with OH_ENABLE_VNC=1. Returns None if VNC isn't exposed for this sandbox."""
        resp = await self._client.get("/api/v1/sandboxes", params={"id": sandbox_id})
        resp.raise_for_status()
        sandboxes = resp.json()
        if not sandboxes or sandboxes[0] is None:
            return None
        for exposed in sandboxes[0].get("exposed_urls") or []:
            if exposed.get("name") == "NOVNC":
                return exposed.get("url")
        return None

    async def search_conversations(self, limit: int = 50) -> list[AppConversation]:
        """Every conversation on the server with its live execution_status/
        sandbox_status, not just what's in the local HistoryStore cache --
        source of truth for a Mission-Control-style task board."""
        resp = await self._client.get("/api/v1/app-conversations/search", params={"limit": limit})
        resp.raise_for_status()
        return [AppConversation.from_json(item) for item in resp.json().get("items", [])]

    async def send_message(
        self,
        conversation_id: str,
        text: str,
        file_attachments: list[str] | None = None,
    ) -> None:
        """Send message with optional file attachments.

        Args:
            conversation_id: Target conversation
            text: Message text
            file_attachments: List of file_ids (from FileManager.upload_file)
        """
        content = [{"type": "text", "text": text}]

        if file_attachments:
            for file_id in file_attachments:
                content.append({"type": "file", "file_id": file_id})

        resp = await self._client.post(
            f"/api/v1/app-conversations/{conversation_id}/send-message",
            json={"role": "user", "content": content},
        )
        resp.raise_for_status()

    async def search_events(
        self, conversation_id: str, *, limit: int = 100, page_id: str | None = None
    ) -> dict:
        """REST fallback for event history (used on load / reconnect, never as
        the live-update path -- live updates come from the WebSocket only).
        """
        params: dict = {"limit": limit}
        if page_id:
            params["page_id"] = page_id
        resp = await self._client.get(
            f"/api/v1/conversation/{conversation_id}/events/search", params=params
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_conversation(self, conversation_id: str) -> None:
        resp = await self._client.delete(f"/api/v1/app-conversations/{conversation_id}")
        resp.raise_for_status()

    async def get_git_changes(self, conversation_id: str, path: str) -> list[dict]:
        """`path` is the absolute repo root inside the sandbox -- confirmed
        live 2026-07-28 to be /workspace/project by default (see
        DEFAULT_WORKSPACE_PATH), since the app-server API has no
        per-conversation way to ask for this directly."""
        resp = await self._client.get(
            f"/api/v1/app-conversations/{conversation_id}/git/changes", params={"path": path}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_git_diff(self, conversation_id: str, path: str, ref: str | None = None) -> dict:
        """Returns {"original": ..., "modified": ...} -- full file contents on
        each side, not a unified diff (confirmed live) -- callers diff them
        client-side."""
        params: dict = {"path": path}
        if ref:
            params["ref"] = ref
        resp = await self._client.get(
            f"/api/v1/app-conversations/{conversation_id}/git/diff", params=params
        )
        resp.raise_for_status()
        return resp.json()
