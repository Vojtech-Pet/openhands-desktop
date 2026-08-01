"""The supervising loop itself. The model proposes one action per turn; this
function is the only thing allowed to decide CONTINUE / RETRY / FINISH / STOP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openhands_desktop.supervised_agent.control import (
    MAX_AGENT_STEPS,
    MAX_AUTO_NUDGES,
    MAX_SAME_RESULT,
    MAX_TOOL_CALLS,
    NO_PROGRESS_WINDOW,
    call_hash,
    evaluate_progress,
    normalize_arguments,
    result_hash,
    verify_task,
)
from openhands_desktop.supervised_agent.llm_client import ActionParseError, LlmClient
from openhands_desktop.supervised_agent.system_prompt import SYSTEM_PROMPT
from openhands_desktop.supervised_agent.tools import Toolbox


def run_agent(
    task: str,
    workspace: str | Path,
    llm: LlmClient,
    on_step: Any = None,
) -> dict:
    """Runs the supervised loop to completion.

    Args:
        task: The user's task description.
        workspace: Directory the agent is allowed to read/write.
        llm: Configured LlmClient.
        on_step: Optional callback(step_info: dict) invoked after each step,
            for live progress reporting (e.g. printing to a CLI).

    Returns:
        A result dict with at least a "status" key: COMPLETED, STUCK,
        WAITING_FOR_USER, or STOPPED.
    """
    tools = Toolbox(workspace)
    workspace_path = tools.workspace

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    tool_call_counts: dict[str, int] = {}
    result_hashes: list[str] = []
    same_result_streak = 0
    progress_history: list[int] = []
    nudges_used = 0
    tool_call_count = 0

    def emit(event: dict) -> None:
        if on_step is not None:
            on_step(event)

    for step in range(1, MAX_AGENT_STEPS + 1):
        try:
            reply = llm.next_action(messages)
        except ActionParseError as exc:
            return {"status": "STOPPED", "reason": f"LLM did not produce a valid action: {exc}", "step": step}

        action = reply.action
        emit({"step": step, "kind": "action", "action": action, "reasoning_preview": reply.reasoning_content[:300]})

        if action["action"] == "FINISH":
            verification = verify_task(workspace_path)
            if verification.success:
                return {
                    "status": "COMPLETED",
                    "message": action.get("message", ""),
                    "verifier": verification.reason,
                    "steps": step,
                }
            emit({"step": step, "kind": "verifier_rejected", "reason": verification.reason})
            messages.append({"role": "assistant", "content": reply.raw_content})
            messages.append(
                {
                    "role": "user",
                    "content": f"Úloha ešte nie je hotová. Verifier: {verification.reason}",
                }
            )
            continue

        if action["action"] == "ASK_USER":
            return {
                "status": "WAITING_FOR_USER",
                "question": action.get("question", ""),
                "steps": step,
            }

        if action["action"] == "PLAN":
            messages.append({"role": "assistant", "content": reply.raw_content})
            continue

        if action["action"] != "CALL_TOOL":
            return {"status": "STOPPED", "reason": f"Neplatná akcia: {action['action']}", "steps": step}

        tool_call_count += 1
        if tool_call_count > MAX_TOOL_CALLS:
            return {"status": "STOPPED", "reason": f"Dosiahnutý limit {MAX_TOOL_CALLS} tool callov.", "steps": step}

        tool_name = action["tool"]
        arguments = normalize_arguments(tool_name, action.get("arguments", {}))
        fingerprint = call_hash(tool_name, arguments)
        count = tool_call_counts.get(fingerprint, 0) + 1
        tool_call_counts[fingerprint] = count

        if count > 1:
            if nudges_used < MAX_AUTO_NUDGES:
                nudges_used += 1
                emit({"step": step, "kind": "nudge", "tool": tool_name, "arguments": arguments})
                messages.append({"role": "assistant", "content": reply.raw_content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "STUCK warning: rovnaký tool call (" + tool_name + ") s rovnakými "
                            "argumentmi už bol vykonaný. Neopakuj ho. Zmeň stratégiu, použi "
                            "existujúci výsledok z histórie, alebo ak je úloha hotová, zvoľ FINISH."
                        ),
                    }
                )
                continue

            return {
                "status": "STUCK",
                "reason": f"Opakovaný rovnaký tool call ({tool_name}) po auto-nudge.",
                "steps": step,
            }

        tool_result = tools.execute(tool_name, arguments)

        current_result_hash = result_hash(tool_result.output)
        duplicate_result = current_result_hash in result_hashes
        result_hashes.append(current_result_hash)
        same_result_streak = same_result_streak + 1 if duplicate_result else 0

        if same_result_streak >= MAX_SAME_RESULT:
            return {
                "status": "STUCK",
                "reason": f"{MAX_SAME_RESULT} kroky po sebe neprinesli žiadnu novú informáciu.",
                "steps": step,
            }

        progress = evaluate_progress(
            tool_name=tool_name,
            result_ok=tool_result.ok,
            duplicate_call=count > 1,
            duplicate_result=duplicate_result,
        )
        progress_history.append(progress)

        emit(
            {
                "step": step,
                "kind": "tool_result",
                "tool": tool_name,
                "arguments": arguments,
                "ok": tool_result.ok,
                "output_preview": tool_result.output[:300],
                "progress": progress,
            }
        )

        messages.append({"role": "assistant", "content": reply.raw_content})
        messages.append({"role": "tool", "name": tool_name, "content": tool_result.output})

        if len(progress_history) >= NO_PROGRESS_WINDOW:
            recent = progress_history[-NO_PROGRESS_WINDOW:]
            if sum(recent) <= 0:
                return {
                    "status": "STUCK",
                    "reason": f"Posledných {NO_PROGRESS_WINDOW} krokov neprinieslo pokrok (súčet skóre <= 0).",
                    "steps": step,
                }

    return {"status": "STOPPED", "reason": "Bol dosiahnutý maximálny počet krokov.", "steps": MAX_AGENT_STEPS}
