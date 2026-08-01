"""Persists user-defined conversation presets (Roo Code / Cline-style named
modes): an agent type + LLM profile + starter instructions bundle a user can
save once and reapply to a new conversation instead of re-picking the same
combination every time. Local-only, no server concept -- reuses the same
sqlite db file as HistoryStore but its own table, since presets and
conversation records have unrelated lifecycles.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import aiosqlite

from openhands_desktop.core.history_store import DEFAULT_DB_PATH


@dataclass
class Preset:
    name: str
    agent_type: str  # "default" or "plan" -- matches AgentType values used elsewhere
    llm_profile_name: str | None
    starter_instructions: str | None


class PresetStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS presets (
                    name TEXT PRIMARY KEY,
                    agent_type TEXT NOT NULL,
                    llm_profile_name TEXT,
                    starter_instructions TEXT
                )
                """
            )
            await db.commit()

    async def save(self, preset: Preset) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO presets (name, agent_type, llm_profile_name, starter_instructions)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    agent_type=excluded.agent_type,
                    llm_profile_name=excluded.llm_profile_name,
                    starter_instructions=excluded.starter_instructions
                """,
                (preset.name, preset.agent_type, preset.llm_profile_name, preset.starter_instructions),
            )
            await db.commit()

    async def delete(self, name: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM presets WHERE name = ?", (name,))
            await db.commit()

    async def list_all(self) -> list[Preset]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM presets ORDER BY name ASC")
            rows = await cursor.fetchall()
        return [
            Preset(
                name=row["name"],
                agent_type=row["agent_type"],
                llm_profile_name=row["llm_profile_name"],
                starter_instructions=row["starter_instructions"],
            )
            for row in rows
        ]
