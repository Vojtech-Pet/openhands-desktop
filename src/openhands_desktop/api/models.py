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
            model=data["model"],
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
