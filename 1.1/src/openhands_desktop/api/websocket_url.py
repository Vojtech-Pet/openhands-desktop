"""Live event stream path for the new (Agent Canvas-era) Agent Server:
one shared server for every conversation, at `/sockets/events/{id}` off
its own base URL, authenticated with the single global session API key as
a query param (verified live 2026-08-01 against agent-server 1.40.0's own
sockets.py -- `_resolve_websocket_session_api_key` accepts it either as
the `x-session-api-key` header or the `session_api_key` query param; a
browser's native WebSocket API cannot set custom headers, so the query
param is the only option a plain client can rely on).

Unlike the old per-conversation sandbox-container model, there is no
per-conversation `conversation_url` to parse -- every conversation lives
on the same server, at the same host:port.
"""

from __future__ import annotations

from urllib.parse import urlparse


def build_websocket_url(conversation_id: str, base_url: str, session_api_key: str | None) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    url = f"{scheme}://{parsed.netloc}/sockets/events/{conversation_id}"
    if session_api_key:
        url += f"?session_api_key={session_api_key}"
    return url
