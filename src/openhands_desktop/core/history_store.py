"""Persists a local record of conversations across app restarts. This is
deliberately a thin, separate cache -- the server remains the source of
truth for conversation state; this store exists so the GUI has something to
show immediately on startup before (or instead of) round-tripping to the
server, and keeps a record of conversations even after the server-side
sandbox/record is gone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~/.local/share/openhands-desktop"), "history.db"
)


@dataclass
class ConversationRecord:
    conversation_id: str
    llm_model: str | None
    title: str | None
    created_at: str
    last_status: str | None


class HistoryStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    llm_model TEXT,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    last_status TEXT
                )
                """
            )
            await db.commit()

    async def record_new(
        self, conversation_id: str, *, llm_model: str | None, title: str | None = None
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO conversations (conversation_id, llm_model, title, created_at, last_status)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    llm_model=excluded.llm_model, title=excluded.title
                """,
                (conversation_id, llm_model, title, created_at),
            )
            await db.commit()

    async def update_status(self, conversation_id: str, status: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE conversations SET last_status = ? WHERE conversation_id = ?",
                (status, conversation_id),
            )
            await db.commit()

    async def delete(self, conversation_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            await db.commit()

    async def list_recent(self, limit: int = 50) -> list[ConversationRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            ConversationRecord(
                conversation_id=row["conversation_id"],
                llm_model=row["llm_model"],
                title=row["title"],
                created_at=row["created_at"],
                last_status=row["last_status"],
            )
            for row in rows
        ]
