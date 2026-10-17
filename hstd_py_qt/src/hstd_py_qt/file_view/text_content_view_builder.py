from pathlib import Path

from beartype import beartype
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

from hstd_py_qt.src.hstd_py_qt.file_view.file_content_builder import (
    FileContentViewBuilder,
)


@beartype
class TextFileContentViewBuilder(FileContentViewBuilder):
    _extra_mimes = {
        "application/json",
        "application/xml",
        "application/x-yaml",
    }

    def can_build(self, mime: str) -> bool:
        return mime.startswith("text/") or mime in self._extra_mimes

    def build(self, absolute_path: str) -> QWidget:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(Path(absolute_path).read_text(errors="replace"))
        return editor
