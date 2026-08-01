"""Mirrors frontend/src/utils/websocket-url.ts: the live event stream is NOT
served by the main app-server API -- the client connects directly to the
conversation's own sandbox agent-server, at a path derived from
`conversation_url`, authenticated with `session_api_key` as a query param.
"""

from __future__ import annotations

from urllib.parse import urlparse


def build_websocket_url(conversation_id: str, conversation_url: str | None, session_api_key: str | None) -> str:
    if conversation_url:
        parsed = urlparse(conversation_url)
        host = parsed.netloc
        # Path prefix is everything before /api/conversations (proxy deployments
        # expose the sandbox under e.g. /runtime/{port}/api/conversations/...).
        prefix = ""
        marker = "/api/conversations"
        if marker in parsed.path:
            prefix = parsed.path.split(marker)[0].rstrip("/")
        scheme = "wss" if parsed.scheme == "https" else "ws"
    else:
        host = "127.0.0.1:3000"
        prefix = ""
        scheme = "ws"

    url = f"{scheme}://{host}{prefix}/sockets/events/{conversation_id}"
    if session_api_key:
        url += f"?session_api_key={session_api_key}"
    return url
