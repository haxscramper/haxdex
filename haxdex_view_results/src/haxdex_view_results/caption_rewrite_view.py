#!/usr/bin/env python
import argparse
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum, auto
from pathlib import Path

import pandas as pd
from beartype import beartype
from beartype.typing import Any, cast
from haxdex_view_results.caption_collection_view import (
    CaptionPreviewConfig,
    CaptionPreviewRow,
    DatasetModel,
    DatasetTable,
    ImageColumn,
    ImageDelegate,
    PreviewColumn,
)
from hstd_py_qt.file_view.column_model import ColumnSpec
from hstd_py_qt.file_view.sci_editor import SciEditor
from PyQt6.Qsci import QsciScintilla
from PyQt6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QObject,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QCloseEvent, QColor, QFocusEvent, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMainWindow,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)


class CaptionSide(Enum):
    ORIGINAL = auto()
    UPDATED = auto()


@beartype
class CaptionDiffRow(CaptionPreviewRow):
    original_caption_path: Path
    updated_caption_path: Path
    original_caption: str

    def caption_path(self, side: CaptionSide) -> Path:
        return (
            self.original_caption_path
            if side == CaptionSide.ORIGINAL
            else self.updated_caption_path
        )

    def caption_text(self, side: CaptionSide) -> str:
        return (
            self.original_caption if side == CaptionSide.ORIGINAL else self.text_caption
        )

    def set_caption_text(self, side: CaptionSide, text: str) -> None:
        if side == CaptionSide.ORIGINAL:
            self.original_caption = text
        else:
            self.text_caption = text


@beartype
@dataclass
class CaptionDiff:
    """UTF-8 byte spans for removed and inserted caption tokens."""

    original: list[tuple[int, int]] = field(default_factory=list)
    updated: list[tuple[int, int]] = field(default_factory=list)


@beartype
def collect_csv_rows(csv_path: Path) -> list[CaptionPreviewRow]:
    rows: list[CaptionPreviewRow] = []

    try:
        frame = pd.read_csv(
            csv_path,
            header=1,
            dtype=str,
            na_filter=False,
            skip_blank_lines=False,
        )
    except pd.errors.EmptyDataError:
        return rows

    for record_number, values in enumerate(
        frame.itertuples(index=False, name=None),
        start=1,
    ):
        if len(values) != 3:
            raise ValueError(
                f"Cannot read {csv_path}, CSV record {record_number}: "
                "expected exactly three columns "
                "(image path, original caption path, updated caption path), "
                f"received {len(values)} columns"
            )

        paths: list[Path] = []
        for position, value in enumerate(values):
            if not value.strip():
                raise ValueError(
                    f"Cannot read {csv_path}, CSV record {record_number}, "
                    f"column {position + 1}: the path is empty"
                )

            path = Path(value).expanduser()
            if not path.is_absolute():
                path = csv_path.parent / path
            paths.append(path.resolve())

        image, original, updated = paths

        if not image.is_file():
            raise FileNotFoundError(
                f"Cannot read image {image}, referenced by {csv_path} "
                f"in CSV record {record_number}: "
                "the path does not exist or is not a regular file"
            )

        rows.append(
            CaptionDiffRow(
                path=image,
                text_caption=updated.read_text(),
                original_caption_path=original,
                updated_caption_path=updated,
                original_caption=original.read_text(),
            )
        )

    return rows


@beartype
def token_byte_offsets(tokens: list[str]) -> list[int]:
    offsets = [0]

    for token in tokens:
        offsets.append(offsets[-1] + len(token.encode("utf-8")))

    return offsets


@beartype
def compare_captions(original: str, updated: str) -> CaptionDiff:
    # Preserve whitespace and punctuation so token spans map exactly to the text.
    original_tokens = re.findall(r"\w+|[^\w\s]|\s+", original)
    updated_tokens = re.findall(r"\w+|[^\w\s]|\s+", updated)

    original_offsets = token_byte_offsets(original_tokens)
    updated_offsets = token_byte_offsets(updated_tokens)

    matcher = SequenceMatcher(
        a=original_tokens,
        b=updated_tokens,
        autojunk=False,
    )
    result = CaptionDiff()

    for (
        operation,
        original_start,
        original_end,
        updated_start,
        updated_end,
    ) in matcher.get_opcodes():
        if operation in ("delete", "replace"):
            result.original.append(
                (
                    original_offsets[original_start],
                    original_offsets[original_end],
                )
            )

        if operation in ("insert", "replace"):
            result.updated.append(
                (
                    updated_offsets[updated_start],
                    updated_offsets[updated_end],
                )
            )

    return result


@beartype
class DiffCaptionEditor(SciEditor):
    editingFinished = pyqtSignal()
    difference_indicator = 8

    def __init__(
        self,
        config: CaptionPreviewConfig,
        side: CaptionSide,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)

        font = QFont()
        if config.caption_font_family is not None:
            font.setFamily(config.caption_font_family)
        font.setPointSize(config.caption_font_size)

        self.setUtf8(True)
        self.setFont(font)
        self.setMargins(0)
        self.setWrapMode(QsciScintilla.WrapMode.WrapWord)
        self.setCaretLineVisible(False)
        self.setIndentationGuides(False)
        self.setBraceMatching(QsciScintilla.BraceMatch.NoBraceMatch)

        color = QColor("#df6060" if side == CaptionSide.ORIGINAL else "#45b875")
        self.indicatorDefine(
            QsciScintilla.IndicatorStyle.StraightBoxIndicator,
            self.difference_indicator,
        )
        self.setIndicatorForegroundColor(color, self.difference_indicator)
        self.setIndicatorDrawUnder(True, self.difference_indicator)
        self.SendScintilla(
            QsciScintilla.SCI_INDICSETALPHA,
            self.difference_indicator,
            65,
        )
        self.SendScintilla(
            QsciScintilla.SCI_INDICSETOUTLINEALPHA,
            self.difference_indicator,
            180,
        )

    def set_differences(self, spans: list[tuple[int, int]]) -> None:
        self.SendScintilla(
            QsciScintilla.SCI_SETINDICATORCURRENT,
            self.difference_indicator,
        )
        self.SendScintilla(
            QsciScintilla.SCI_INDICATORCLEARRANGE,
            0,
            self.SendScintilla(QsciScintilla.SCI_GETLENGTH),
        )

        for start, end in spans:
            self.SendScintilla(
                QsciScintilla.SCI_INDICATORFILLRANGE,
                start,
                end - start,
            )

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.editingFinished.emit()


@beartype
class DiffCaptionDelegate(QStyledItemDelegate):
    captionChanged = pyqtSignal(int)

    def __init__(
        self,
        config: CaptionPreviewConfig,
        side: CaptionSide,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.side = side

    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> DiffCaptionEditor:
        editor = DiffCaptionEditor(self.config, self.side, parent)
        row = index.row()

        editor.editingFinished.connect(lambda: self.commitData.emit(editor))
        editor.textChanged.connect(lambda: self.captionChanged.emit(row))
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        if isinstance(editor, DiffCaptionEditor):
            text = index.data(Qt.ItemDataRole.EditRole)
            if editor.text() != text:
                editor.setText(text)

    def setModelData(
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex,
    ) -> None:
        if isinstance(editor, DiffCaptionEditor):
            model.setData(
                index,
                editor.text(),
                Qt.ItemDataRole.EditRole,
            )

    def updateEditorGeometry(
        self,
        editor: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        margin = self.config.caption_editor_margin
        editor.setGeometry(option.rect.adjusted(margin, margin, -margin, -margin))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, DiffCaptionEditor):
            return False
        return super().eventFilter(watched, event)


@beartype
class DiffCaptionColumn(PreviewColumn):
    def __init__(
        self,
        title: str,
        side: CaptionSide,
        delegate: DiffCaptionDelegate,
    ) -> None:
        super().__init__(title, delegate)
        self.side = side

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        row = cast(CaptionDiffRow, index.internalPointer())

        match role:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                return row.caption_text(self.side)
            case Qt.ItemDataRole.ToolTipRole:
                return str(row.caption_path(self.side))
            case Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            case _:
                return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return super().flags(index) | Qt.ItemFlag.ItemIsEditable

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole:
            return False

        row = cast(CaptionDiffRow, index.internalPointer())
        path = row.caption_path(self.side)

        if not isinstance(value, str):
            raise TypeError(
                f"Cannot save caption {path}: expected a string, "
                f"received {type(value).__name__}"
            )

        if value == row.caption_text(self.side):
            return False

        path.write_text(value)
        row.set_caption_text(self.side, value)
        return True


@beartype
class CaptionDiffWindow(QMainWindow):
    def __init__(
        self,
        csv_path: Path,
        rows: list[CaptionPreviewRow],
    ) -> None:
        super().__init__()

        self.config = CaptionPreviewConfig(
            directory=csv_path.parent,
            show_wd_tags=False,
            window_width=1600,
            window_height=900,
            image_column_width=420,
            preview_width=400,
            preview_height=360,
            row_height=400,
        )

        self.table = DatasetTable(self)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.table.setWordWrap(False)
        self.setCentralWidget(self.table)

        self.image_delegate = ImageDelegate(self.table, self.config)
        self.original_delegate = DiffCaptionDelegate(
            self.config,
            CaptionSide.ORIGINAL,
            self.table,
        )
        self.updated_delegate = DiffCaptionDelegate(
            self.config,
            CaptionSide.UPDATED,
            self.table,
        )

        columns: list[ColumnSpec] = [
            ImageColumn("Image", self.image_delegate),
            DiffCaptionColumn(
                "Original caption — removals",
                CaptionSide.ORIGINAL,
                self.original_delegate,
            ),
            DiffCaptionColumn(
                "Updated caption — additions",
                CaptionSide.UPDATED,
                self.updated_delegate,
            ),
        ]

        self.dataset_model = DatasetModel(rows, columns, self)
        self.table.setModel(self.dataset_model)
        self.dataset_model.configureView(self.table)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, self.config.image_column_width)
        self.table.verticalHeader().setDefaultSectionSize(self.config.row_height)

        self.original_delegate.captionChanged.connect(self.refresh_diff)
        self.updated_delegate.captionChanged.connect(self.refresh_diff)

        for row in range(self.dataset_model.rowCount()):
            self.table.openPersistentEditor(self.dataset_model.index(row, 1))
            self.table.openPersistentEditor(self.dataset_model.index(row, 2))
            self.refresh_diff(row)

        self.setWindowTitle(f"Caption differences — {csv_path}")
        self.resize(
            self.config.window_width,
            self.config.window_height,
        )

    def refresh_diff(self, row_number: int) -> None:
        row = cast(
            CaptionDiffRow,
            self.dataset_model.rows[row_number],
        )
        original_editor = self.table.indexWidget(
            self.dataset_model.index(row_number, 1)
        )
        updated_editor = self.table.indexWidget(self.dataset_model.index(row_number, 2))

        original = (
            original_editor.text()
            if isinstance(original_editor, DiffCaptionEditor)
            else row.original_caption
        )
        updated = (
            updated_editor.text()
            if isinstance(updated_editor, DiffCaptionEditor)
            else row.text_caption
        )

        differences = compare_captions(original, updated)

        if isinstance(original_editor, DiffCaptionEditor):
            original_editor.set_differences(differences.original)

        if isinstance(updated_editor, DiffCaptionEditor):
            updated_editor.set_differences(differences.updated)

    def commit_editors(self) -> None:
        delegates = (
            (1, self.original_delegate),
            (2, self.updated_delegate),
        )

        for row in range(self.dataset_model.rowCount()):
            for column, delegate in delegates:
                index = self.dataset_model.index(row, column)
                editor = self.table.indexWidget(index)

                if isinstance(editor, DiffCaptionEditor):
                    delegate.setModelData(
                        editor,
                        self.dataset_model,
                        index,
                    )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.commit_editors()
        super().closeEvent(event)


@beartype
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "View and edit word-level caption differences. "
            "Input must be a headerless CSV containing image path, "
            "original caption path, and updated caption path. "
            "Relative paths resolve against the CSV directory."
        )
    )
    parser.add_argument("csv", type=Path)
    arguments = parser.parse_args()

    csv_path = arguments.csv.expanduser().resolve()

    application = QApplication([sys.argv[0]])
    rows = collect_csv_rows(csv_path)
    window = CaptionDiffWindow(csv_path, rows)
    window.show()

    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
