"""Shared helper for firing an async client call from a Qt slot and
surfacing success/failure back on the Qt thread -- used by any dialog that
talks to AppServerClient directly (settings pages, diff viewer) rather than
routing every call through MainWindow.
"""

from __future__ import annotations

import asyncio

import httpx
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox, QWidget


def error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            data = exc.response.json()
            for key in ("detail", "error", "message"):
                if key in data:
                    return str(data[key])
        except Exception:  # noqa: BLE001 -- body wasn't JSON; fall through to text
            pass
        return exc.response.text or str(exc)
    return str(exc)


def run_async(parent: QWidget, coro, on_success=None, *, error_title: str = "Request failed") -> None:
    async def _runner():
        try:
            result = await coro
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
            QTimer.singleShot(0, lambda: QMessageBox.warning(parent, error_title, error_detail(exc)))
            return
        if on_success is not None:
            # Deferred, not called inline: on_success handlers sometimes pop
            # a modal QMessageBox of their own (e.g. TaskBoardDialog's
            # "some deletions failed" warning) -- calling that .exec() while
            # this coroutine's own Task is still on the stack (still inside
            # asyncio.ensure_future(_runner())'s frame) hits a qasync
            # reentrancy guard, confirmed live 2026-08-01: "RuntimeError:
            # Cannot enter into task <...> while another task <...> is
            # being executed" repeated for every other pending task (health
            # checks, model polling) the instant the modal's nested Qt
            # event loop started pumping events. Scheduling on_success via
            # QTimer.singleShot(0, ...) instead runs it only after this
            # Task has actually finished and control has returned to the
            # loop, so any modal it shows is no longer nested inside it.
            QTimer.singleShot(0, lambda: on_success(result))

    asyncio.ensure_future(_runner())
