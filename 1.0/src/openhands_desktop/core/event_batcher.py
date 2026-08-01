"""Coalesces the live event stream before it reaches any Qt view.

Why this exists: the OpenHands *web* frontend calls its Zustand store's
`addEvent` once per token during streaming, and that store does an immutable
`[...array]` copy of the whole accumulated event list on every call (Zustand
needs a new reference to trigger re-renders). On a long-running conversation
with thousands of accumulated events, that copy is O(n) per token, which is
the leading suspect for the browser tab freezing on long runs (found
2026-07-27, comparing against LM Studio's own chat UI, which has no such
per-token history list). A Qt list/table model has the same shape of problem
if you feed it one row-changed signal per token.

This batcher merges consecutive StreamingDeltaEvents (one per generated
token) into a single event before emitting, on a fixed timer, so a Qt view
gets at most ~1 update per interval instead of one per token. Non-delta
events always flush any pending delta first, preserving stream order.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from openhands_desktop.core.events import EventKind, NormalizedEvent, parse_event

DEFAULT_FLUSH_INTERVAL_MS = 40


class EventBatcher(QObject):
    events_ready = Signal(list)  # list[NormalizedEvent]

    def __init__(self, flush_interval_ms: int = DEFAULT_FLUSH_INTERVAL_MS, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending_delta: dict[str, Any] | None = None
        self._pending: list[NormalizedEvent] = []
        self._seen_ids: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(flush_interval_ms)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.flush)

    def ingest(self, raw_event: dict[str, Any]) -> None:
        # Non-delta events carry a stable id and can arrive twice: once from
        # the REST history backfill (used to see events that happened before
        # a WebSocket could be connected -- e.g. the initial SystemPromptEvent
        # and first user MessageEvent, which are created as part of
        # conversation startup itself) and once from the WebSocket replaying
        # the tail of that same history on connect. StreamingDeltaEvents have
        # no such id and are never backfilled, so they're exempt.
        event_id = raw_event.get("id")
        if event_id and raw_event.get("kind") != EventKind.STREAMING_DELTA.value:
            if event_id in self._seen_ids:
                return
            self._seen_ids.add(event_id)

        event = parse_event(raw_event)

        if event.kind == EventKind.STREAMING_DELTA:
            if self._pending_delta is None:
                self._pending_delta = dict(raw_event)
            else:
                self._pending_delta["content"] = (self._pending_delta.get("content") or "") + (
                    raw_event.get("content") or ""
                )
                self._pending_delta["reasoning_content"] = (
                    self._pending_delta.get("reasoning_content") or ""
                ) + (raw_event.get("reasoning_content") or "")
            if not self._timer.isActive():
                self._timer.start()
            return

        # A genuine event ends the current delta run -- flush it first so it
        # lands before this event, not after.
        self._flush_delta()
        self._pending.append(event)
        if not self._timer.isActive():
            self._timer.start()

    def _flush_delta(self) -> None:
        if self._pending_delta is not None:
            self._pending.append(parse_event(self._pending_delta))
            self._pending_delta = None

    def flush(self) -> None:
        self._timer.stop()
        self._flush_delta()
        if self._pending:
            batch, self._pending = self._pending, []
            self.events_ready.emit(batch)
