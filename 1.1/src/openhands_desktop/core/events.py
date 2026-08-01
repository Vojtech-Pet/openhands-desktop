"""Normalizes raw OpenHands SDK event JSON into a small, stable shape the UI
depends on, so an upstream SDK rename doesn't ripple into every widget.

Shapes verified live against a running agent-server on 2026-07-27:
- The discriminator field on every event is "kind" (e.g. "ActionEvent",
  "ObservationEvent", "MessageEvent", "StreamingDeltaEvent",
  "ConversationStateUpdateEvent", "SystemPromptEvent", "HookExecutionEvent").
- ActionEvent carries the real tool name directly in `tool_name` (e.g.
  "glob", "terminal", "finish") -- not to be confused with the nested
  `action.kind` (e.g. "GlobAction"), which is the action's own type name.
- ObservationEvent mirrors this with `tool_name` + `observation`.

Also verified live (2026-07-30), against a real conversation that ended in
the "error" execution_status shown in the sidebar/history list: the actual
reason lives in a "ConversationErrorEvent" with a `code` (e.g.
"MaxIterationsReached") and human-readable `detail` -- a *different* kind
from "AgentErrorEvent", not previously in this enum, so it fell through to
OTHER and _render_event silently dropped it. The sidebar/history status
still showed "error" (that's server-polled separately), but nothing ever
told the user *why* in the chat itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    ACTION = "ActionEvent"
    OBSERVATION = "ObservationEvent"
    MESSAGE = "MessageEvent"
    STREAMING_DELTA = "StreamingDeltaEvent"
    CONVERSATION_STATE_UPDATE = "ConversationStateUpdateEvent"
    SYSTEM_PROMPT = "SystemPromptEvent"
    HOOK_EXECUTION = "HookExecutionEvent"
    AGENT_ERROR = "AgentErrorEvent"
    CONVERSATION_ERROR = "ConversationErrorEvent"
    CONDENSATION = "CondensationSummaryEvent"
    OTHER = "Other"


@dataclass
class NormalizedEvent:
    kind: EventKind
    raw_kind: str
    event_id: str
    source: str
    tool_name: str | None
    text: str | None
    timestamp: datetime | None
    raw: dict[str, Any]

    @property
    def is_finish_action(self) -> bool:
        return self.kind == EventKind.ACTION and self.tool_name == "finish"


def _parse_timestamp(raw: dict) -> datetime | None:
    raw_ts = raw.get("timestamp")
    if not raw_ts:
        return None
    try:
        return datetime.fromisoformat(raw_ts)
    except ValueError:
        return None


def _extract_text(raw: dict) -> str | None:
    """Best-effort plain-text extraction for display, without assuming which
    event kind carries it (message text vs observation text vs delta chunk).
    """
    llm_message = raw.get("llm_message")
    if llm_message and isinstance(llm_message.get("content"), list):
        parts = [c.get("text") for c in llm_message["content"] if c.get("type") == "text"]
        if parts:
            return "".join(p for p in parts if p)

    observation = raw.get("observation")
    if observation and isinstance(observation.get("content"), list):
        parts = [
            c.get("text")
            for c in observation["content"]
            if isinstance(c, dict) and c.get("text")
        ]
        if parts:
            return "".join(p for p in parts if p)

    if observation:
        for key in ("error", "message", "detail"):
            value = observation.get(key)
            if isinstance(value, str) and value.strip():
                return value

    if "text" in raw:
        return raw.get("text")

    return None


def parse_event(raw: dict[str, Any]) -> NormalizedEvent:
    raw_kind = raw.get("kind", "Other")
    try:
        kind = EventKind(raw_kind)
    except ValueError:
        kind = EventKind.OTHER

    return NormalizedEvent(
        kind=kind,
        raw_kind=raw_kind,
        event_id=raw.get("id", ""),
        source=raw.get("source", ""),
        tool_name=raw.get("tool_name"),
        text=_extract_text(raw),
        timestamp=_parse_timestamp(raw),
        raw=raw,
    )
