"""Real test of HistoryStore against an actual temp SQLite file."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openhands_desktop.core.history_store import HistoryStore


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/history.db"
        store = HistoryStore(db_path)
        await store.init()

        await store.record_new("conv-1", llm_model="openai/model-a", title="First task")
        await store.record_new("conv-2", llm_model="openai/model-b", title="Second task")
        await store.update_status("conv-1", "COMPLETED")

        records = await store.list_recent()
        print(f"count: {len(records)}")
        for r in records:
            print(f"  {r.conversation_id} model={r.llm_model} title={r.title!r} status={r.last_status}")

        assert len(records) == 2
        assert records[0].conversation_id == "conv-2"  # most recent first
        assert records[1].last_status == "COMPLETED"

        # Re-open a fresh HistoryStore instance against the same file
        # (simulating app restart) -- data must survive.
        store2 = HistoryStore(db_path)
        await store2.init()
        records2 = await store2.list_recent()
        assert len(records2) == 2
        print("PASS: real SQLite persistence across a fresh HistoryStore instance")

        # Upsert behavior: re-recording an existing id updates fields, not duplicates.
        await store.record_new("conv-1", llm_model="openai/model-a2", title="First task (renamed)")
        records3 = await store.list_recent()
        assert len(records3) == 2, "expected upsert, not a new row"
        updated = next(r for r in records3 if r.conversation_id == "conv-1")
        assert updated.llm_model == "openai/model-a2"
        assert updated.last_status == "COMPLETED", "status should survive the upsert"
        print("PASS: upsert updates fields without duplicating rows or losing status")


if __name__ == "__main__":
    asyncio.run(main())
