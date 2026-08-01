"""File upload/download manager for large files (videos, images, etc).

Handles:
- Chunked uploads (resume-able)
- Progress tracking
- Cleanup
- HTTP streaming for large downloads
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Callable

import httpx


class FileManager:
    """Manages large file transfers."""

    def __init__(self, api_client: httpx.AsyncClient, storage_dir: str | None = None):
        self._client = api_client
        self.storage_dir = Path(storage_dir or "/tmp/openhands_files")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._uploads_in_progress: dict[str, dict] = {}

    async def upload_file(
        self,
        file_path: str,
        conversation_id: str,
        on_progress: Callable[[int, int], None] | None = None,
        chunk_size: int = 50_000_000,  # 50MB chunks
    ) -> str:
        """Upload large file in chunks.

        Args:
            file_path: Local file to upload
            conversation_id: Where to upload it
            on_progress: Callback(bytes_done, total_bytes)
            chunk_size: Chunk size in bytes

        Returns:
            file_id for use in messages
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        file_hash = self._hash_file(file_path)
        upload_id = f"{conversation_id}_{file_hash}"
        self._uploads_in_progress[upload_id] = {"status": "starting", "bytes_sent": 0}

        try:
            total_chunks = (file_size + chunk_size - 1) // chunk_size

            with open(file_path, "rb") as f:
                for chunk_num in range(total_chunks):
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    await self._client.post(
                        f"/api/v1/app-conversations/{conversation_id}/upload-chunk",
                        content=chunk,
                        headers={
                            "X-Upload-ID": upload_id,
                            "X-Chunk-Number": str(chunk_num),
                            "X-Total-Chunks": str(total_chunks),
                            "X-File-Name": file_path.name,
                        },
                    )

                    bytes_sent = (chunk_num + 1) * len(chunk)
                    self._uploads_in_progress[upload_id]["bytes_sent"] = bytes_sent
                    if on_progress:
                        on_progress(bytes_sent, file_size)

            self._uploads_in_progress[upload_id]["status"] = "completed"
            return upload_id

        except Exception as e:
            self._uploads_in_progress[upload_id]["status"] = "failed"
            raise e

    async def download_file(
        self,
        file_id: str,
        output_path: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download file with streaming.

        Args:
            file_id: File to download
            output_path: Where to save (default: storage_dir/file_id)
            on_progress: Callback(bytes_done, total_bytes)

        Returns:
            Path to downloaded file
        """
        if output_path is None:
            output_path = str(self.storage_dir / file_id)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with self._client.stream(
            "GET", f"/api/v1/files/{file_id}/download"
        ) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            bytes_downloaded = 0

            with open(output_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1_000_000):
                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    if on_progress and total_size:
                        on_progress(bytes_downloaded, total_size)

        return str(output_path)

    def get_upload_progress(self, upload_id: str) -> dict | None:
        """Get progress of an in-progress upload."""
        return self._uploads_in_progress.get(upload_id)

    def cleanup_file(self, file_id: str) -> None:
        """Delete a file from storage."""
        file_path = self.storage_dir / file_id
        if file_path.exists():
            file_path.unlink()

    @staticmethod
    def _hash_file(file_path: Path, algorithm: str = "sha256") -> str:
        """Compute file hash."""
        hasher = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
