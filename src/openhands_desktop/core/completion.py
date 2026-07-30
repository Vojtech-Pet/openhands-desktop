"""Distinguishes "the server reports finished" from "the task actually
finished".

Found 2026-07-27: a Plan-mode run's `execution_status` went to "finished"
after ~30 minutes without ever writing real plan content -- the model's
final turn was plain text with no tool call and no explicit `finish` action,
and the framework treated the text-only turn as done anyway (see
local-project/.openhands/hooks/stop_syntax_check.sh for the server-side
mitigation used there). A desktop client has no equivalent stop-hook to lean
on, so it must make this judgment itself from the event stream: only trust
"finished" once a genuine ActionEvent with tool_name == "finish" has been
seen for this run.

"For this run" is load-bearing: a single conversation can have several user
messages, each starting its own run. Naively tracking "was finish() ever
seen anywhere in this conversation's history" would let a *stale* finish()
from an earlier, already-completed run falsely confirm a *new* run --
concretely, if the client ever backfills history for a conversation that
already finished once before (e.g. reopening/resuming an existing
conversation, or the WS-first sync in conversation_controller.py replaying
old history after a reset), that old finish() must not count. Scoping by
event timestamp against the moment the current run started closes this:
only a finish() timestamped at or after the current run's start belongs to
it. This is a client-side approximation (host wall-clock vs. the sandbox's
own event timestamps) -- the SDK doesn't expose a run/turn id to key off of
instead; if it ever does, switch to that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto

from openhands_desktop.api.models import ExecutionStatus
from openhands_desktop.core.events import NormalizedEvent


class RunState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    WAITING_FOR_CONFIRMATION = auto()
    FINISHED_UNVERIFIED = auto()  # server says finished, no finish() action seen
    COMPLETED = auto()  # server says finished AND a genuine finish() action was seen
    STUCK = auto()
    ERROR = auto()
    DELETING = auto()


_SERVER_STATUS_TO_STATE = {
    ExecutionStatus.IDLE: RunState.IDLE,
    ExecutionStatus.RUNNING: RunState.RUNNING,
    ExecutionStatus.PAUSED: RunState.PAUSED,
    ExecutionStatus.WAITING_FOR_CONFIRMATION: RunState.WAITING_FOR_CONFIRMATION,
    ExecutionStatus.STUCK: RunState.STUCK,
    ExecutionStatus.ERROR: RunState.ERROR,
    ExecutionStatus.DELETING: RunState.DELETING,
}


@dataclass
class CompletionTracker:
    """Tracks, per run (i.e. since the last user message was sent), whether a
    genuine finish() action has occurred *for that run specifically*. Call
    `observe_event` for every event as it arrives, `reset_for_new_run` when
    the user sends a message (or a fresh conversation starts), and `resolve`
    whenever a fresh `execution_status` comes in.
    """

    _finish_action_seen: bool = False
    _run_start_time: datetime | None = field(default=None)

    def reset_for_new_run(self) -> None:
        self._finish_action_seen = False
        # Naive UTC, matching the server's own (also naive) event
        # timestamps -- datetime.now(timezone.utc) is tz-aware and would
        # raise on comparison against them.
        self._run_start_time = datetime.now(timezone.utc).replace(tzinfo=None)

    def reset_for_attach(self) -> None:
        """For attaching to an *existing* conversation (resuming from
        history), not starting a new one: there is no "current run" boundary
        to speak of yet -- we haven't sent a message, we're just observing
        whatever state the conversation is already in. Unlike
        reset_for_new_run(), the boundary is set far in the past so a
        finish() from any point in the conversation's real history counts,
        letting an already-completed conversation correctly resolve as
        COMPLETED on attach rather than FINISHED_UNVERIFIED. The next actual
        send_message() call still calls reset_for_new_run() as normal, which
        correctly re-scopes to "from now on" for that new run.
        """
        self._finish_action_seen = False
        self._run_start_time = datetime.min

    def observe_event(self, event: NormalizedEvent) -> None:
        if not event.is_finish_action:
            return
        if self._run_start_time is None:
            # No run has been started yet (observe_event called before the
            # first reset_for_new_run) -- can't be in-scope for anything.
            return
        if event.timestamp is not None and event.timestamp < self._run_start_time:
            return  # stale finish() from a previous run's history
        self._finish_action_seen = True

    def resolve(self, server_status: ExecutionStatus | None) -> RunState:
        if server_status is None:
            return RunState.IDLE
        if server_status == ExecutionStatus.FINISHED:
            return RunState.COMPLETED if self._finish_action_seen else RunState.FINISHED_UNVERIFIED
        return _SERVER_STATUS_TO_STATE.get(server_status, RunState.IDLE)
