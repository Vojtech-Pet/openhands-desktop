"""Error dialog for displaying detailed error messages."""

from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QTextEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)
from PySide6.QtCore import Qt


class ErrorDialog(QDialog):
    """Dialog to display detailed error information."""

    def __init__(self, error_type: str, message: str, details: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Error: {error_type}")
        self.setGeometry(100, 100, 600, 400)

        layout = QVBoxLayout(self)

        # Error type header
        header = QLabel(f"<b>{error_type}</b>")
        header.setStyleSheet("color: #ff6b6b; font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(header)

        # Main message
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 12px; margin-bottom: 15px;")
        layout.addWidget(msg_label)

        # Details section (if provided)
        if details:
            details_label = QLabel("<b>Details:</b>")
            layout.addWidget(details_label)

            details_text = QTextEdit()
            details_text.setPlainText(details)
            details_text.setReadOnly(True)
            details_text.setMaximumHeight(150)
            details_text.setStyleSheet("background-color: #2b2b2b; color: #d4d4d4; font-family: monospace;")
            layout.addWidget(details_text)

        # Buttons
        button_layout = QHBoxLayout()
        copy_btn = QPushButton("Copy Error")
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(error_type, message, details))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        button_layout.addWidget(copy_btn)
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)

    def _copy_to_clipboard(self, error_type: str, message: str, details: str | None) -> None:
        """Copy error info to clipboard."""
        from PySide6.QtGui import QClipboard
        from PySide6.QtWidgets import QApplication

        text = f"Error: {error_type}\n{message}\n"
        if details:
            text += f"\n{details}"

        clipboard = QApplication.clipboard()
        clipboard.setText(text)


def show_error(
    error_type: str,
    message: str,
    details: str | None = None,
    parent=None,
) -> None:
    """Show error dialog with formatted message."""
    dialog = ErrorDialog(error_type, message, details, parent)
    dialog.exec()
