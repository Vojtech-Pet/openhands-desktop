"""Data models mirroring the real OpenHands app-server API shapes.

Field names and endpoint shapes here were verified live against a running
agent-server (port 3000) on 2026-07-27, not guessed from documentation --
see StartTaskStatus / AppConversation for the fields that actually matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StartTaskStatus(str, Enum):
    WORKING = "WORKING"
    WAITING_FOR_SANDBOX = "WAITING_FOR_SANDBOX"
    PREPARING_REPOSITORY = "PREPARING_REPOSITORY"
    RUNNING_SETUP_SCRIPT = "RUNNING_SETUP_SCRIPT"
    SETTING_UP_GIT_HOOKS = "SETTING_UP_GIT_HOOKS"
    SETTING_UP_SKILLS = "SETTING_UP_SKILLS"
    STARTING_CONVERSATION = "STARTING_CONVERSATION"
    READY = "READY"
    ERROR = "ERROR"


# Mirrors openhands.sdk.ConversationExecutionStatus exactly (verified live,
# 2026-07-27). "finished" is deliberately NOT treated as a synonym for "the
# task is actually done" -- see core/completion.py. A model can end its turn
# with plain text and no finish() tool call, and the server still reports
# "finished".
class ExecutionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    FINISHED = "finished"
    ERROR = "error"
    STUCK = "stuck"
    DELETING = "deleting"


@dataclass
class AppConversationStartTask:
    id: str
    status: StartTaskStatus
    detail: str | None = None
    app_conversation_id: str | None = None
    sandbox_id: str | None = None
    agent_server_url: str | None = None

    @classmethod
    def from_json(cls, data: dict) -> "AppConversationStartTask":
        return cls(
            id=data["id"],
            status=StartTaskStatus(data["status"]),
            detail=data.get("detail"),
            app_conversation_id=data.get("app_conversation_id"),
            sandbox_id=data.get("sandbox_id"),
            agent_server_url=data.get("agent_server_url"),
        )


@dataclass
class LlmProfile:
    name: str
    model: str
    base_url: str | None
    api_key_set: bool = False

    @classmethod
    def from_json(cls, data: dict) -> "LlmProfile":
        return cls(
            name=data["name"],
            # The current app-server profile summary allows ``model`` to be
            # null.  Keep the desktop UI usable while such a profile is being
            # repaired instead of failing while building its label.
            model=str(data.get("model") or ""),
            base_url=data.get("base_url"),
            api_key_set=data.get("api_key_set", False),
        )


@dataclass
class AppConversation:
    id: str
    title: str | None
    llm_model: str | None
    sandbox_status: str
    execution_status: ExecutionStatus | None
    conversation_url: str | None
    session_api_key: str | None
    sandbox_id: str | None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict) -> "AppConversation":
        raw_status = data.get("execution_status")
        return cls(
            id=data["id"],
            title=data.get("title"),
            llm_model=data.get("llm_model"),
            sandbox_status=data.get("sandbox_status", "MISSING"),
            execution_status=ExecutionStatus(raw_status) if raw_status else None,
            conversation_url=data.get("conversation_url"),
            session_api_key=data.get("session_api_key"),
            sandbox_id=data.get("sandbox_id"),
            raw=data,
        )


def resolve_conversation_family(conversation_id: str, all_conversations: list["AppConversation"]) -> set[str]:
    """Every conversation id sharing a sandbox with `conversation_id` (a
    Continue-as-Code parent/child pair, walked in both directions).

    Deliberately does NOT read `sub_conversation_ids` -- confirmed live
    2026-07-31 that it comes back empty even on a parent with a real,
    running child, on both the list and single-conversation endpoints, with
    or without `include_sub_conversations=true`. `parent_conversation_id`
    IS reliable (with that query flag -- see AppServerClient.get_conversation
    /search_conversations), so the family is resolved by walking up to the
    root via that field, then back down by scanning every conversation in
    `all_conversations` for a `parent_conversation_id` matching something
    already found -- a real graph traversal, not a single hop, so a chain
    deeper than one Plan->Code step still resolves correctly.
    """
    by_id = {c.id: c for c in all_conversations}
    family = {conversation_id}
    root_id = conversation_id
    while True:
        conversation = by_id.get(root_id)
        parent_id = conversation.raw.get("parent_conversation_id") if conversation else None
        if not parent_id or parent_id in family:
            break
        family.add(parent_id)
        root_id = parent_id
    changed = True
    while changed:
        changed = False
        for conversation in all_conversations:
            if conversation.id in family:
                continue
            if conversation.raw.get("parent_conversation_id") in family:
                family.add(conversation.id)
                changed = True
    return family
