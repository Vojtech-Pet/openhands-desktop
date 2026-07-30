"""Talks to LM Studio's OpenAI-compatible endpoint and parses the model's
reply into one structured action. The model is asked (via response_format)
to return JSON only -- reasoning/thinking, if the model produces any, comes
back separately in `reasoning_content` and is never treated as the action.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["PLAN", "CALL_TOOL", "ASK_USER", "FINISH"]},
        "reason": {"type": "string"},
        "tool": {"type": "string"},
        "arguments": {"type": "object"},
        "message": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["action", "reason"],
}


class ActionParseError(Exception):
    pass


@dataclass
class LlmReply:
    action: dict[str, Any]
    reasoning_content: str
    raw_content: str


class LlmClient:
    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str | None = None,
        max_output_tokens: int = 8192,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=300.0)

    def close(self) -> None:
        self._client.close()

    def next_action(self, messages: list[dict[str, Any]], retries: int = 2) -> LlmReply:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_output_tokens,
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "presence_penalty": 1.5,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "agent_action",
                        "strict": True,
                        "schema": ACTION_SCHEMA,
                    },
                },
                "repeat_penalty": 1.0,
                "dry_multiplier": 0.8,
                "dry_base": 1.75,
                "dry_allowed_length": 2,
            }
            try:
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            choice = data["choices"][0]["message"]
            content = choice.get("content") or ""
            reasoning = choice.get("reasoning_content") or ""

            try:
                action = _extract_json(content)
                _validate_action(action)
            except (ActionParseError, ValueError) as exc:
                last_error = exc
                # Feed the parse error back so the model can self-correct
                # instead of silently retrying the same malformed output.
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            f"Tvoja odpoveď nebola platný JSON podľa schémy "
                            f"(action/reason/tool/arguments/message/question). Chyba: {exc}. "
                            "Odpovedz znova, iba jeden platný JSON objekt, nič iné."
                        ),
                    },
                ]
                continue

            return LlmReply(action=action, reasoning_content=reasoning, raw_content=content)

        raise ActionParseError(f"model did not return a valid action after {retries + 1} attempts: {last_error}")


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    # Some local models wrap JSON in a ```json fence even when told not to.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ActionParseError(f"no JSON object found in: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ActionParseError(f"invalid JSON: {exc}") from exc


def _validate_action(action: dict[str, Any]) -> None:
    if "action" not in action:
        raise ValueError("missing 'action' field")
    if action["action"] not in ("PLAN", "CALL_TOOL", "ASK_USER", "FINISH"):
        raise ValueError(f"invalid action value: {action['action']!r}")
    if action["action"] == "CALL_TOOL" and "tool" not in action:
        raise ValueError("CALL_TOOL requires a 'tool' field")
