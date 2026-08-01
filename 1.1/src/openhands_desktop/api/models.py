"""Data models for the new (Agent Canvas-era) OpenHands Agent Server API --
one long-running server instance hosting many conversations directly, no
app-server orchestrator and no per-conversation sandbox container/session
key. Field names verified live against openhands-agent-server 1.40.0's own
OpenAPI schema and real request/response bodies on 2026-08-01 (see
`agent_server_client.py`), not guessed from documentation.

This replaces the old two-tier app-server (port 3000, orchestrates N
per-conversation sandbox containers) + SandboxConversationClient (talks to
one conversation's own container) split -- there is now exactly one server
to talk to for everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# Mirrors openhands.sdk.ConversationExecutionStatus exactly (verified live
# 2026-08-01 against agent-server 1.40.0 -- identical enum to the old
# app-server's, since both ultimately come from the same SDK). "finished" is
# deliberately NOT treated as a synonym for "the task is actually done" --
# see core/completion.py. A model can end its turn with plain text and no
# finish() tool call, and the server still reports "finished".
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
    execution_status: ExecutionStatus | None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict) -> "AppConversation":
        raw_status = data.get("execution_status")
        agent = data.get("agent") or {}
        llm = agent.get("llm") or {}
        return cls(
            id=data["id"],
            title=data.get("title"),
            llm_model=llm.get("model"),
            execution_status=ExecutionStatus(raw_status) if raw_status else None,
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
