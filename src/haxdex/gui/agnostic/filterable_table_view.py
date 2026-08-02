from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTableView,
    QWidget,
)
from beartype import beartype
from beartype.typing import Any


@beartype
@dataclass(frozen=True, slots=True)
class FilterColumnConfig:
    column: int
    placeholder: str


@beartype
class ColumnFilterHeaderView(QWidget):
    filter_changed = pyqtSignal(int, str)

    def __init__(self, table: QTableView) -> None:
        super().__init__(table.viewport())
        self.table = table
        self.editors: dict[int, QLineEdit] = {}
        self.margin = 1
        self.hide()

    def configure(self, columns: list[FilterColumnConfig]) -> None:
        for editor in self.editors.values():
            editor.deleteLater()
        self.editors.clear()

        for item in columns:
            editor = QLineEdit(self)
            editor.setPlaceholderText(item.placeholder)
            editor.textChanged.connect(
                lambda text, column=item.column: self.filter_changed.emit(column, text))
            self.editors[item.column] = editor

        self.apply_viewport_margin()
        self.update_positions()
        self.setVisible(bool(self.editors))

    def preferred_height(self) -> int:
        if not self.editors:
            return 0
        editor = next(iter(self.editors.values()))
        return editor.sizeHint().height() + (self.margin * 2)

    def update_positions(self) -> None:
        if not self.editors:
            return

        height = self.preferred_height()
        self.setGeometry(0, 0, self.table.viewport().width(), height)

        for column, editor in self.editors.items():
            if self.table.isColumnHidden(column):
                editor.hide()
                continue

            x = self.table.columnViewportPosition(column)
            width = self.table.columnWidth(column)

            editor.setGeometry(
                x + self.margin,
                self.margin,
                max(0, width - (self.margin * 2)),
                max(0, height - (self.margin * 2)),
            )
            editor.show()

    def apply_viewport_margin(self) -> None:
        self.table.setViewportMargins(0, self.preferred_height(), 0, 0)


@beartype
class FilterableTableView(QTableView):
    column_filter_changed = pyqtSignal(int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.filter_header = ColumnFilterHeaderView(self)
        self.filter_header.filter_changed.connect(self.column_filter_changed.emit)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.verticalHeader().setVisible(True)
        self.verticalHeader().setDefaultSectionSize(18)
        self.setSortingEnabled(True)

        header = self.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(lambda *_: self.filter_header.update_positions())
        header.sectionMoved.connect(lambda *_: self.filter_header.update_positions())

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.horizontalScrollBar().valueChanged.connect(
            lambda _value: self.filter_header.update_positions())

        self.viewport().installEventFilter(self)

    def configure_filters(self, columns: list[FilterColumnConfig]) -> None:
        self.filter_header.configure(columns)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self.filter_header.update_positions()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.filter_header.update_positions()
