from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import magic
from beartype import beartype
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from haxdex.gui.collection_views.file_content_view.file_content_builder import FileContentViewBuilder
from haxdex.gui.collection_views.file_content_view.image_content_view_builder import ImageFileContentViewBuilder
from haxdex.gui.collection_views.file_content_view.pdf_content_view_builder import PdfFileContentViewBuilder
from haxdex.gui.collection_views.file_content_view.text_content_view_builder import TextFileContentViewBuilder
from haxdex.gui.collection_views.file_content_view.video_content_view_builder import VideoFileContentViewBuilder
from haxdex.gui.common.qt_utils import get_settings
from haxdex.gui.file_tree.actions.action_handler import ActionResult
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.types import FileHash
from haxdex.services.utils import format_timestamp_relative

ABSOLUTE_PATHS_QUERY = """
LET file = DOCUMENT("files", @file_key)
FOR entry IN file.paths
  LET root = DOCUMENT("roots", entry.root.name)
  RETURN CONCAT_SEPARATOR("/", root.path, entry.relative)
""".strip()


@dataclass(frozen=True)
class PathRow:
    path: str
    created: str
    modified: str


@beartype
class PathsTableModel(QAbstractTableModel):
    _headers = ("File path", "Created", "Modified")

    def __init__(self, paths: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[PathRow] = [self._row_from_path(p) for p in paths]

    def _row_from_path(self, path: str) -> PathRow | None:
        if Path(path).exists():
            stat = Path(path).stat()
            created_ts = getattr(stat, "st_birthtime", stat.st_ctime)
            modified_ts = stat.st_mtime

        else:
            created_ts = None
            modified_ts = None

        return PathRow(
            path=path,
            created=format_timestamp_relative(created_ts),
            modified=format_timestamp_relative(modified_ts),
        )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 3

    def data(self,
             index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None

        row = self._rows[index.row()]
        column = index.column()

        if column == 0:
            return row.path
        if column == 1:
            return row.created
        if column == 2:
            return row.modified
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._headers[section]
        return str(section + 1)


class ShrinkableLabel(QLabel):

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        return QSize(0, 0)


@beartype
class DispatchingFileContentPreviewBuilder:

    def __init__(self) -> None:
        self._magic = magic.Magic(mime=True)
        self._builders: list[FileContentViewBuilder] = [
            PdfFileContentViewBuilder(),
            ImageFileContentViewBuilder(),
            VideoFileContentViewBuilder(),
            TextFileContentViewBuilder(),
        ]

    def build(self, absolute_path: str) -> QWidget:
        if Path(absolute_path).exists():
            mime = self._magic.from_file(absolute_path)
            for builder in self._builders:
                if builder.can_build(mime):
                    widget = builder.build(absolute_path)
                    widget.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Expanding)
                    widget.setMinimumSize(0, 0)
                    return widget

            fallback = ShrinkableLabel(f"Unsupported file type: {mime}\n{absolute_path}")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setWordWrap(True)
            fallback.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
            fallback.setMinimumSize(0, 0)
            return fallback

        else:
            fallback = ShrinkableLabel(f"Path does not exist {absolute_path}")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setWordWrap(True)
            fallback.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
            fallback.setMinimumSize(0, 0)
            return fallback


@beartype
class PathsWidgetBuilder(WidgetBuilder):

    def __init__(self) -> None:
        self.absolute_paths: list[str] = []
        self.settings = get_settings()
        self.header_state_key = "paths_widget/table_header_state"

        self.root: QWidget | None = None
        self.preview_host: QWidget | None = None
        self.preview_layout: QVBoxLayout | None = None
        self.table: QTableView | None = None
        self.result_previews_host: QWidget | None = None
        self.result_previews_layout: QVBoxLayout | None = None
        self.model: PathsTableModel | None = None

    def _load_absolute_paths(self, db: IndexDatabase, file_hash: FileHash) -> list[str]:
        cursor = db._db.aql.execute(
            ABSOLUTE_PATHS_QUERY,
            bind_vars={"file_key": file_hash.hash},
        )
        return [str(path) for path in cursor]

    def _save_header_state(self, logical_index: int, old_size: int,
                           new_size: int) -> None:
        if self.table is None:
            return
        self.settings.setValue(
            self.header_state_key,
            self.table.horizontalHeader().saveState(),
        )

    def _restore_header_state(self) -> None:
        if self.table is None:
            return
        state = self.settings.value(self.header_state_key, None)
        if state is not None:
            self.table.horizontalHeader().restoreState(state)

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _ensure_ui(self) -> None:
        if self.root is not None:
            return

        self.root = QWidget()
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)

        root_layout = QVBoxLayout(self.root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_host = QWidget(self.root)
        assert self.preview_host is not None
        self.preview_host.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        self.preview_layout = QVBoxLayout(self.preview_host)
        assert self.preview_layout is not None
        self.preview_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableView(self.root)
        assert self.table is not None
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setWordWrap(False)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        vh = self.table.verticalHeader()
        assert vh is not None
        vh.setVisible(False)
        hh = self.table.horizontalHeader()
        assert hh is not None
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.sectionResized.connect(self._save_header_state)

        self.result_previews_host = QWidget(self.root)
        self.result_previews_host.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.result_previews_layout = QVBoxLayout(self.result_previews_host)
        assert self.result_previews_layout is not None
        self.result_previews_layout.setContentsMargins(0, 0, 0, 0)

        root_layout.addWidget(self.preview_host, 3)
        root_layout.addWidget(self.table, 2)
        root_layout.addWidget(self.result_previews_host, 2)

        self._restore_header_state()

    def _set_preview(self) -> None:
        assert self.preview_layout is not None
        self._clear_layout(self.preview_layout)

        if self.absolute_paths:
            preview = DispatchingFileContentPreviewBuilder().build(self.absolute_paths[0])
            self.preview_layout.addWidget(preview)
        else:
            empty = QLabel("No paths found")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
            self.preview_layout.addWidget(empty)

    def _set_result_previews(self, action_result: ActionResult | None) -> None:
        assert self.result_previews_layout is not None
        self._clear_layout(self.result_previews_layout)

        if action_result is None:
            return

        for path in action_result.getResultPaths():
            preview = DispatchingFileContentPreviewBuilder().build(str(path))
            self.result_previews_layout.addWidget(preview)

    def build(
        self,
        db: IndexDatabase,
        hash: FileHash,
        action_result: ActionResult | None = None,
    ) -> QWidget:
        self._ensure_ui()
        assert self.root is not None
        assert self.table is not None

        self.absolute_paths = self._load_absolute_paths(db, hash)
        self._set_preview()
        self._set_result_previews(action_result)

        self.model = PathsTableModel(self.absolute_paths, self.table)
        self.table.setModel(self.model)
        self._restore_header_state()

        self.root.paths_model = self.model
        return self.root
