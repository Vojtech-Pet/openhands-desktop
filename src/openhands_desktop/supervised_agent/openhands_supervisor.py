"""Supervises a REAL OpenHands conversation instead of bypassing it.

Unlike run_agent.py (which talks directly to LM Studio with a small toolset
of its own), this drives an actual ConversationController -- OpenHands keeps
its full toolset (browser, VSCode, terminal, real sandbox) -- and applies the
same hash-based repeat detection, progress scoring, and 1-nudge-then-stop
policy from control.py against its real event stream, instead of trusting
the SDK's own stuck detector (whose threshold isn't configurable from here --
see the 2026-07-30 conversation history) or leaving loops to run unwatched.

ConversationWatchdog attaches to a controller that already exists (and that
someone else drives the lifecycle of) -- this is what lets main_window.py
supervise every normal conversation automatically (2026-07-31), not just the
ones explicitly started through the Supervised Agent dialog.
supervise_openhands_task keeps its own controller + start_new for that
dialog's standalone use.
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
    MAX_SAME_RESULT,
    NO_PROGRESS_WINDOW,
    call_hash,
    evaluate_progress,
    normalize_arguments,
    result_hash,
)

# Escalating, not repeated verbatim -- 2026-07-31: with a single generic
# nudge (the old MAX_AUTO_NUDGES=1 from control.py, still used by
# run_agent.py's standalone path), a genuinely stuck agent just tried the
# *same* repeated call a third time and got hard-stopped into PAUSED,
# needing the user to type something by hand every time. Each step here
# asks for something more different than the last -- a different tool, then
# skip the sub-task entirely, then wrap up -- before finally giving up.
NUDGE_TEXTS = [
    "STUCK warning: rovnaký tool call s rovnakými argumentmi už bol vykonaný. "
    "Neopakuj ho. Zmeň stratégiu, použi existujúci výsledok z histórie, alebo "
    "ak je úloha hotová, zvoľ finish.",
    "Stále opakuješ ten istý krok aj po predchádzajúcom upozornení. Prestaň to "
    "skúšať znova rovnako -- vyber úplne iný nástroj alebo úplne iný prístup k "
    "tejto konkrétnej časti úlohy.",
    "Toto sa opakovane nedarí aj po zmene prístupu. Vzdaj sa tejto konkrétnej "
    "podúlohy, zhrň v odpovedi čo sa doteraz podarilo a čo presne blokuje "
    "zvyšok, a pokračuj ďalšou časťou úlohy (alebo zavolaj finish, ak nič iné "
    "neostáva).",
]
MAX_AUTO_NUDGES = len(NUDGE_TEXTS)


async def _interrupt_and_nudge(controller: ConversationController, text: str) -> None:
    """Confirmed live 2026-07-30: sending a follow-up message immediately
    after interrupt() races the interrupt (it only "takes effect in ~1s" per
    ConversationController.interrupt's own docstring) -- the nudge message
    reaches the conversation but the agent never picks it back up, leaving
    execution_status stuck at PAUSED forever. main_window.py's GUI auto-nudge
    already accounts for this with the same delay; this path needs it too."""
    controller.interrupt()
    await asyncio.sleep(1.5)
    controller.send_message(text)


class ConversationWatchdog:
    """Hash-dedup + progress-scoring watchdog over an *existing*
    ConversationController's live event stream. Doesn't create the
    conversation or own its lifecycle -- just watches, nudges once on a
    repeat, and stops (interrupt only) if that doesn't help. `on_step` is
    called on the qasync loop for every action/observation/nudge/stuck/state
    event, same shape supervise_openhands_task already emits, so callers
    (the Supervised Agent dialog, or main_window's own log) can render it
    the same way either path produced it.
    """

    def __init__(
        self,
        controller: ConversationController,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._controller = controller
        self._on_step = on_step
        self._attached = False
        self.reset()
        controller.events_received.connect(self._on_events)
        controller.state_changed.connect(self._on_state_changed)
        self._attached = True

    def reset(self) -> None:
        """Called when the same controller starts a *new* conversation
        (e.g. main_window.py reuses one ConversationWatchdog across the
        window's lifetime, one conversation at a time) -- otherwise counts
        from a finished conversation would bleed into the next one."""
        self._tool_call_counts: dict[str, int] = {}
        self._result_hashes: list[str] = []
        self._same_result_streak = 0
        self._progress_history: list[int] = []
        self._nudges_used = 0
        self._action_count = 0
        self._stopped = False

    @property
    def action_count(self) -> int:
        return self._action_count

    def detach(self) -> None:
        if not self._attached:
            return
        try:
            self._controller.events_received.disconnect(self._on_events)
            self._controller.state_changed.disconnect(self._on_state_changed)
        except (RuntimeError, TypeError):
            pass
        self._attached = False

    def _emit(self, event: dict[str, Any]) -> None:
        if self._on_step is not None:
            self._on_step(event)

    def _on_events(self, events: list[NormalizedEvent]) -> None:
        if self._stopped:
            return
        for event in events:
            if event.kind == EventKind.ACTION:
                if event.tool_name == "finish":
                    continue
                self._action_count += 1
                if self._action_count > MAX_AGENT_STEPS:
                    self._stopped = True
                    self._controller.interrupt()
                    self._emit(
                        {"kind": "stuck", "reason": f"Dosiahnutý limit {MAX_AGENT_STEPS} krokov."}
                    )
                    return

                tool_name = event.tool_name or "?"
                arguments = normalize_arguments(tool_name, _extract_arguments(event.raw))
                fingerprint = call_hash(tool_name, arguments)
                count = self._tool_call_counts.get(fingerprint, 0) + 1
                self._tool_call_counts[fingerprint] = count

                self._emit({"kind": "action", "tool": tool_name, "arguments": arguments, "repeat_count": count})

                if count > 1:
                    if self._nudges_used < MAX_AUTO_NUDGES:
                        nudge_text = NUDGE_TEXTS[self._nudges_used]
                        self._nudges_used += 1
                        self._emit(
                            {
                                "kind": "nudge",
                                "tool": tool_name,
                                "attempt": self._nudges_used,
                                "of": MAX_AUTO_NUDGES,
                            }
                        )
                        asyncio.ensure_future(_interrupt_and_nudge(self._controller, nudge_text))
                    else:
                        self._stopped = True
                        self._emit(
                            {
                                "kind": "stuck",
                                "reason": (
                                    f"Opakovaný tool call ({tool_name}) aj po {MAX_AUTO_NUDGES} "
                                    "rôznych auto-nudge pokusoch."
                                ),
                            }
                        )
                        self._controller.interrupt()
                        return

            elif event.kind == EventKind.OBSERVATION:
                if event.tool_name == "finish":
                    continue
                text = event.text or ""
                current_hash = result_hash(text)
                duplicate_result = current_hash in self._result_hashes
                self._result_hashes.append(current_hash)
                self._same_result_streak = self._same_result_streak + 1 if duplicate_result else 0

                self._emit(
                    {
                        "kind": "observation",
                        "tool": event.tool_name,
                        "preview": text[:300],
                        "duplicate_result": duplicate_result,
                    }
                )

                if self._same_result_streak >= MAX_SAME_RESULT:
                    self._stopped = True
                    self._controller.interrupt()
                    self._emit(
                        {
                            "kind": "stuck",
                            "reason": f"{MAX_SAME_RESULT} kroky po sebe neprinesli žiadnu novú informáciu.",
                        }
                    )
                    return

                progress = evaluate_progress(
                    tool_name=event.tool_name or "?",
                    result_ok="ERROR" not in text[:20].upper(),
                    duplicate_call=False,
                    duplicate_result=duplicate_result,
                )
                self._progress_history.append(progress)

                if len(self._progress_history) >= NO_PROGRESS_WINDOW:
                    recent = self._progress_history[-NO_PROGRESS_WINDOW:]
                    if sum(recent) <= 0:
                        self._stopped = True
                        self._controller.interrupt()
                        self._emit(
                            {
                                "kind": "stuck",
                                "reason": f"Posledných {NO_PROGRESS_WINDOW} krokov neprinieslo pokrok.",
                            }
                        )
                        return

    def _on_state_changed(self, state: RunState) -> None:
        if self._stopped:
            return
        self._emit({"kind": "state", "state": state.name})


async def supervise_openhands_task(
    task: str,
    client: AppServerClient,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    llm_model: str | None = None,
    agent_type: str = "default",
) -> dict[str, Any]:
    """Starts a new OpenHands conversation for `task` and watches it with a
    ConversationWatchdog. Returns a result dict shaped like run_agent.py's:
    at least a "status" key (COMPLETED, FINISHED_UNVERIFIED, STUCK, STOPPED,
    ERROR)."""
    controller = ConversationController(client)
    done: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
    caller_on_step = on_step

    def finish(result: dict[str, Any]) -> None:
        if not done.done():
            done.set_result(result)

    def watch_step(event: dict[str, Any]) -> None:
        if caller_on_step is not None:
            caller_on_step(event)
        if done.done():
            return
        if event["kind"] == "stuck":
            finish({"status": "STUCK", "reason": event["reason"], "steps": watchdog.action_count})
        elif event["kind"] == "state":
            state = RunState[event["state"]]
            if state == RunState.COMPLETED:
                finish({"status": "COMPLETED", "steps": watchdog.action_count})
            elif state == RunState.FINISHED_UNVERIFIED:
                finish({"status": "FINISHED_UNVERIFIED", "steps": watchdog.action_count})
            elif state == RunState.ERROR:
                finish(
                    {
                        "status": "ERROR",
                        "reason": "Conversation reported an error state.",
                        "steps": watchdog.action_count,
                    }
                )

    watchdog = ConversationWatchdog(controller, on_step=watch_step)

    def on_error(message: str) -> None:
        if caller_on_step is not None:
            caller_on_step({"kind": "error", "message": message})

    controller.error_occurred.connect(on_error)

    try:
        controller.start_new(llm_model=llm_model, initial_message=task, agent_type=agent_type)
        result = await done
    finally:
        controller.error_occurred.disconnect(on_error)
        watchdog.detach()
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
