"""Direct client for the sandbox's OWN agent-server REST API (reached via
`conversation_url`, not the main app-server on port 3000). This is where the
real pause/interrupt/condense controls live -- confirmed in the SDK source
(`openhands/agent_server/conversation_router.py`):

- POST {conversation_url}/pause: waits for the current LLM call to finish,
  then stops -- a "soft" stop.
- POST {conversation_url}/interrupt: cancels the in-flight LLM request
  immediately -- a "hard" stop, effective instantly.
- POST {conversation_url}/condense: forces condensation of the conversation
  history right now, rather than waiting for the automatic condenser
  threshold (max_size/max_tokens) to trigger -- the equivalent of Claude
  Code's /compact.

Auth is the per-conversation `session_api_key`, sent as the `X-Session-API-Key`
header (the same key also accepted as a query param for the WebSocket).
"""

from __future__ import annotations

import httpx


class SandboxConversationClient:
    def __init__(self, conversation_url: str, session_api_key: str | None) -> None:
        self._conversation_url = conversation_url.rstrip("/")
        headers = {"X-Session-API-Key": session_api_key} if session_api_key else {}
        self._client = httpx.AsyncClient(headers=headers, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def pause(self) -> bool:
        resp = await self._client.post(f"{self._conversation_url}/pause")
        resp.raise_for_status()
        return True

    async def interrupt(self) -> bool:
        resp = await self._client.post(f"{self._conversation_url}/interrupt")
        resp.raise_for_status()
        return True

    async def condense(self) -> bool:
        resp = await self._client.post(f"{self._conversation_url}/condense")
        resp.raise_for_status()
        return True
