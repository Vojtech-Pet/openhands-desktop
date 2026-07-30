"""Supervises a REAL OpenHands conversation instead of bypassing it.

Unlike run_agent.py (which talks directly to LM Studio with a small toolset
of its own), this drives an actual ConversationController -- OpenHands keeps
its full toolset (browser, VSCode, terminal, real sandbox) -- and applies the
same hash-based repeat detection, progress scoring, and 1-nudge-then-stop
policy from control.py against its real event stream, instead of trusting
the SDK's own stuck detector (whose threshold isn't configurable from here --
see the 2026-07-30 conversation history) or leaving loops to run unwatched.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from openhands_desktop.api.client import AppServerClient
from openhands_desktop.core.completion import RunState
from openhands_desktop.core.conversation_controller import ConversationController
from openhands_desktop.core.events import EventKind, NormalizedEvent
from openhands_desktop.supervised_agent.control import (
    MAX_AGENT_STEPS,
    MAX_AUTO_NUDGES,
    MAX_SAME_RESULT,
    NO_PROGRESS_WINDOW,
    call_hash,
    evaluate_progress,
    normalize_arguments,
    result_hash,
)

NUDGE_TEXT = (
    "STUCK warning: rovnaký tool call s rovnakými argumentmi už bol vykonaný. "
    "Neopakuj ho. Zmeň stratégiu, použi existujúci výsledok z histórie, alebo "
    "ak je úloha hotová, zvoľ finish."
)


async def _interrupt_and_nudge(controller: ConversationController) -> None:
    """Confirmed live 2026-07-30: sending a follow-up message immediately
    after interrupt() races the interrupt (it only "takes effect in ~1s" per
    ConversationController.interrupt's own docstring) -- the nudge message
    reaches the conversation but the agent never picks it back up, leaving
    execution_status stuck at PAUSED forever. main_window.py's GUI auto-nudge
    already accounts for this with the same delay; this path needs it too."""
    controller.interrupt()
    await asyncio.sleep(1.5)
    controller.send_message(NUDGE_TEXT)


async def supervise_openhands_task(
    task: str,
    client: AppServerClient,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    llm_model: str | None = None,
    agent_type: str = "default",
) -> dict[str, Any]:
    """Starts a new OpenHands conversation for `task` and watches it.

    Returns a result dict shaped like run_agent.py's: at least a "status" key
    (COMPLETED, FINISHED_UNVERIFIED, STUCK, STOPPED, ERROR).
    """
    controller = ConversationController(client)
    done: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()

    def emit(event: dict[str, Any]) -> None:
        if on_step is not None:
            on_step(event)

    def finish(result: dict[str, Any]) -> None:
        if not done.done():
            done.set_result(result)

    tool_call_counts: dict[str, int] = {}
    result_hashes: list[str] = []
    same_result_streak = 0
    progress_history: list[int] = []
    nudges_used = 0
    action_count = 0
    stopped = False

    def on_error(message: str) -> None:
        emit({"kind": "error", "message": message})

    def on_events(events: list[NormalizedEvent]) -> None:
        nonlocal action_count, nudges_used, same_result_streak, stopped
        if stopped or done.done():
            return
        for event in events:
            if event.kind == EventKind.ACTION:
                if event.tool_name == "finish":
                    continue  # real completion is handled via state_changed
                action_count += 1
                if action_count > MAX_AGENT_STEPS:
                    stopped = True
                    controller.interrupt()
                    finish({"status": "STOPPED", "reason": f"Dosiahnutý limit {MAX_AGENT_STEPS} krokov."})
                    return

                tool_name = event.tool_name or "?"
                arguments = _extract_arguments(event.raw)
                arguments = normalize_arguments(tool_name, arguments)
                fingerprint = call_hash(tool_name, arguments)
                count = tool_call_counts.get(fingerprint, 0) + 1
                tool_call_counts[fingerprint] = count

                emit({"kind": "action", "tool": tool_name, "arguments": arguments, "repeat_count": count})

                if count > 1:
                    if nudges_used < MAX_AUTO_NUDGES:
                        nudges_used += 1
                        emit({"kind": "nudge", "tool": tool_name})
                        asyncio.ensure_future(_interrupt_and_nudge(controller))
                    else:
                        stopped = True
                        emit({"kind": "stuck", "reason": f"Opakovaný tool call ({tool_name}) po auto-nudge."})
                        controller.interrupt()
                        finish(
                            {
                                "status": "STUCK",
                                "reason": f"Opakovaný rovnaký tool call ({tool_name}) po auto-nudge.",
                                "steps": action_count,
                            }
                        )
                        return

            elif event.kind == EventKind.OBSERVATION:
                if event.tool_name == "finish":
                    continue
                text = event.text or ""
                current_hash = result_hash(text)
                duplicate_result = current_hash in result_hashes
                result_hashes.append(current_hash)
                same_result_streak = same_result_streak + 1 if duplicate_result else 0

                emit(
                    {
                        "kind": "observation",
                        "tool": event.tool_name,
                        "preview": text[:300],
                        "duplicate_result": duplicate_result,
                    }
                )

                if same_result_streak >= MAX_SAME_RESULT:
                    stopped = True
                    controller.interrupt()
                    finish(
                        {
                            "status": "STUCK",
                            "reason": f"{MAX_SAME_RESULT} kroky po sebe neprinesli žiadnu novú informáciu.",
                            "steps": action_count,
                        }
                    )
                    return

                progress = evaluate_progress(
                    tool_name=event.tool_name or "?",
                    result_ok="ERROR" not in text[:20].upper(),
                    duplicate_call=False,
                    duplicate_result=duplicate_result,
                )
                progress_history.append(progress)

                if len(progress_history) >= NO_PROGRESS_WINDOW:
                    recent = progress_history[-NO_PROGRESS_WINDOW:]
                    if sum(recent) <= 0:
                        stopped = True
                        controller.interrupt()
                        finish(
                            {
                                "status": "STUCK",
                                "reason": f"Posledných {NO_PROGRESS_WINDOW} krokov neprinieslo pokrok.",
                                "steps": action_count,
                            }
                        )
                        return

    def on_state_changed(state: RunState) -> None:
        if stopped or done.done():
            return
        emit({"kind": "state", "state": state.name})
        if state == RunState.COMPLETED:
            finish({"status": "COMPLETED", "steps": action_count})
        elif state == RunState.FINISHED_UNVERIFIED:
            finish({"status": "FINISHED_UNVERIFIED", "steps": action_count})
        elif state == RunState.ERROR:
            finish({"status": "ERROR", "reason": "Conversation reported an error state.", "steps": action_count})

    controller.error_occurred.connect(on_error)
    controller.events_received.connect(on_events)
    controller.state_changed.connect(on_state_changed)

    try:
        controller.start_new(llm_model=llm_model, initial_message=task, agent_type=agent_type)
        result = await done
    finally:
        controller.error_occurred.disconnect(on_error)
        controller.events_received.disconnect(on_events)
        controller.state_changed.disconnect(on_state_changed)
        await controller.stop()

    return result


def _extract_arguments(raw_event: dict[str, Any]) -> dict[str, Any]:
    """ActionEvent.raw carries the tool call as a JSON *string* in
    tool_call.arguments (confirmed live 2026-07-30), not a parsed dict --
    NormalizedEvent.text doesn't cover ActionEvents at all (see events.py's
    _extract_text), so this reads it straight from the raw payload."""
    tool_call = raw_event.get("tool_call") or {}
    raw_args = tool_call.get("arguments")
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {"_raw": raw_args}
    return {}
