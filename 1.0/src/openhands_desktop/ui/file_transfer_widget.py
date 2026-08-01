"""Widget for uploading/downloading large files with progress tracking."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from openhands_desktop.api.file_manager import FileManager


class FileTransferDialog(QDialog):
    """Dialog for uploading/downloading files."""

    file_uploaded = Signal(str)  # file_id
    file_downloaded = Signal(str)  # file_path

    def __init__(self, file_manager: FileManager, conversation_id: str, parent=None):
        super().__init__(parent)
        self.file_manager = file_manager
        self.conversation_id = conversation_id
        self._upload_task: asyncio.Task | None = None
        self._download_task: asyncio.Task | None = None

        self.setWindowTitle("File Transfer")
        self.setGeometry(100, 100, 400, 150)

        layout = QVBoxLayout(self)

        self.label = QLabel("Ready")
        layout.addWidget(self.label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        button_layout = QVBoxLayout()
        self.upload_btn = QPushButton("Upload Video")
        self.upload_btn.clicked.connect(self._on_upload_clicked)
        button_layout.addWidget(self.upload_btn)

        self.download_btn = QPushButton("Download Output")
        self.download_btn.clicked.connect(self._on_download_clicked)
        button_layout.addWidget(self.download_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def _on_upload_clicked(self):
        from PySide6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "", "Video Files (*.mp4 *.mov *.mkv);;All Files (*)"
        )
        if file_path:
            self.upload_btn.setEnabled(False)
            self._upload_task = asyncio.create_task(self._upload_file(file_path))

    def _on_download_clicked(self):
        from PySide6.QtWidgets import QFileDialog

        output_path, _ = QFileDialog.getSaveFileName(
            self, "Save Video As", "", "Video Files (*.mp4);;All Files (*)"
        )
        if output_path:
            self.download_btn.setEnabled(False)
            # TODO: get file_id from agent output
            self._download_task = asyncio.create_task(self._download_file("output_video", output_path))

    async def _upload_file(self, file_path: str):
        try:
            self.label.setText(f"Uploading {Path(file_path).name}...")

            def on_progress(done: int, total: int):
                pct = int(100 * done / total) if total > 0 else 0
                self.progress.setValue(pct)
                self.label.setText(f"Uploading... {pct}%")

            file_id = await self.file_manager.upload_file(
                file_path, self.conversation_id, on_progress=on_progress
            )

            self.label.setText(f"Upload complete!")
            self.progress.setValue(100)
            self.file_uploaded.emit(file_id)

        except Exception as e:
            self.label.setText(f"Upload failed: {e}")
        finally:
            self.upload_btn.setEnabled(True)

    async def _download_file(self, file_id: str, output_path: str):
        try:
            self.label.setText("Downloading video...")

            def on_progress(done: int, total: int):
                pct = int(100 * done / total) if total > 0 else 0
                self.progress.setValue(pct)
                self.label.setText(f"Downloading... {pct}%")

            result = await self.file_manager.download_file(
                file_id, output_path, on_progress=on_progress
            )

            self.label.setText(f"Download complete: {Path(result).name}")
            self.progress.setValue(100)
            self.file_downloaded.emit(result)

        except Exception as e:
            self.label.setText(f"Download failed: {e}")
        finally:
            self.download_btn.setEnabled(True)
