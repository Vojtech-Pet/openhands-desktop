"""Validates `conversation_url` before it's used to build a WebSocket
connection or to send the per-conversation `session_api_key` anywhere.

`conversation_url` comes from the app-server's own response, which the
client is already trusting -- but it's still worth checking the shape is
sane before handing a secret to whatever host it names, in case of a
misconfigured or compromised app-server: a malformed value (embedded
credentials, an unexpected scheme, an empty host) is a clear signal
something is wrong, and the secret should not be sent regardless of how
that value was produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


@dataclass
class UrlValidationResult:
    ok: bool
    reason: str | None = None


def validate_conversation_url(url: str | None, *, app_server_base_url: str) -> UrlValidationResult:
    if not url:
        return UrlValidationResult(False, "conversation_url is empty")

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return UrlValidationResult(False, f"conversation_url is not a valid URL: {exc}")

    if parsed.scheme not in ALLOWED_SCHEMES:
        return UrlValidationResult(
            False, f"conversation_url has an unexpected scheme {parsed.scheme!r} (expected http/https)"
        )

    if parsed.username or parsed.password:
        return UrlValidationResult(False, "conversation_url must not embed credentials")

    if not parsed.hostname:
        return UrlValidationResult(False, "conversation_url has no host")

    app_server_hostname = urlparse(app_server_base_url).hostname

    if parsed.hostname not in LOCAL_HOSTNAMES and parsed.hostname != app_server_hostname:
        return UrlValidationResult(
            False,
            f"conversation_url host {parsed.hostname!r} is neither localhost nor the "
            f"configured app-server host {app_server_hostname!r} -- refusing to send "
            "the session key there",
        )

    return UrlValidationResult(True)
