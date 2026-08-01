"""Real test of CompletionTracker's run-scoping: a stale finish() from a
previous run's backfilled history must NOT count toward the current run,
while a genuine finish() timestamped after the reset must."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openhands_desktop.api.models import ExecutionStatus
from openhands_desktop.core.completion import CompletionTracker, RunState
from openhands_desktop.core.events import parse_event

# --- Scenario 1: stale finish() from an earlier run must not count ---
tracker = CompletionTracker()
tracker.reset_for_new_run()

# Simulate a finish() that happened BEFORE this run started (e.g. backfilled
# history from a conversation that already completed once before).
stale_finish_raw = {
    "id": "old-finish",
    "kind": "ActionEvent",
    "tool_name": "finish",
    "source": "agent",
    "timestamp": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
}
tracker.observe_event(parse_event(stale_finish_raw))

state = tracker.resolve(ExecutionStatus.FINISHED)
print(f"scenario 1 (stale finish from before reset): resolved to {state}")
assert state == RunState.FINISHED_UNVERIFIED, "stale finish() incorrectly counted toward the current run!"
print("PASS: stale finish() from before the run boundary correctly ignored")

# --- Scenario 2: a genuine finish() timestamped after reset DOES count ---
tracker2 = CompletionTracker()
tracker2.reset_for_new_run()
time.sleep(0.05)  # ensure a real, measurable gap

genuine_finish_raw = {
    "id": "new-finish",
    "kind": "ActionEvent",
    "tool_name": "finish",
    "source": "agent",
    "timestamp": datetime.utcnow().isoformat(),
}
tracker2.observe_event(parse_event(genuine_finish_raw))
state2 = tracker2.resolve(ExecutionStatus.FINISHED)
print(f"scenario 2 (genuine finish after reset): resolved to {state2}")
assert state2 == RunState.COMPLETED, "genuine finish() after reset was NOT recognized!"
print("PASS: genuine finish() after the run boundary correctly recognized as COMPLETED")

# --- Scenario 3: realistic sequence -- run 1 completes, reset, run 2's
# backfill (which would include run 1's finish()) must not falsely confirm
# run 2 before run 2's own finish() arrives. ---
tracker3 = CompletionTracker()
tracker3.reset_for_new_run()
run1_finish_time = datetime.utcnow()
tracker3.observe_event(parse_event({
    "id": "run1-finish", "kind": "ActionEvent", "tool_name": "finish",
    "source": "agent", "timestamp": run1_finish_time.isoformat(),
}))
assert tracker3.resolve(ExecutionStatus.FINISHED) == RunState.COMPLETED

# New message sent -> new run starts.
tracker3.reset_for_new_run()
# Backfill/replay could reintroduce run 1's finish() (same event, old timestamp).
tracker3.observe_event(parse_event({
    "id": "run1-finish", "kind": "ActionEvent", "tool_name": "finish",
    "source": "agent", "timestamp": run1_finish_time.isoformat(),
}))
state3 = tracker3.resolve(ExecutionStatus.FINISHED)
print(f"scenario 3 (run 2 before its own finish, run 1's finish replayed): resolved to {state3}")
assert state3 == RunState.FINISHED_UNVERIFIED, "run 1's stale finish() falsely confirmed run 2!"
print("PASS: replaying run 1's finish() during run 2 does not falsely confirm run 2")
