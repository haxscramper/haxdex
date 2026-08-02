from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTableView,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class FilterColumnConfig:
    column: int
    placeholder: str


class ColumnFilterHeaderView(QWidget):
    filter_changed = pyqtSignal(int, str)

    def __init__(self, table: QTableView) -> None:
        super().__init__(table.viewport())
        self._table = table
        self._editors: dict[int, QLineEdit] = {}
        self._margin = 1
        self.hide()

    def configure(self, columns: list[FilterColumnConfig]) -> None:
        for editor in self._editors.values():
            editor.deleteLater()
        self._editors.clear()

        for item in columns:
            editor = QLineEdit(self)
            editor.setPlaceholderText(item.placeholder)
            editor.textChanged.connect(
                lambda text, col=item.column: self.filter_changed.emit(col, text))
            self._editors[item.column] = editor

        self._apply_viewport_margin()
        self.update_positions()
        self.setVisible(bool(self._editors))

    def preferred_height(self) -> int:
        if not self._editors:
            return 0
        any_editor = next(iter(self._editors.values()))
        return any_editor.sizeHint().height() + (self._margin * 2)

    def update_positions(self) -> None:
        if not self._editors:
            return

        total_height = self.preferred_height()
        self.setGeometry(0, 0, self._table.viewport().width(), total_height)

        for column, editor in self._editors.items():
            if self._table.isColumnHidden(column):
                editor.hide()
                continue

            x = self._table.columnViewportPosition(column)
            width = self._table.columnWidth(column)

            editor.setGeometry(
                x + self._margin,
                self._margin,
                max(0, width - (self._margin * 2)),
                max(0, total_height - (self._margin * 2)),
            )
            editor.show()

    def _apply_viewport_margin(self) -> None:
        self._table.setViewportMargins(0, self.preferred_height(), 0, 0)


class FilterableTableView(QTableView):
    column_filter_changed = pyqtSignal(int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._filter_header = ColumnFilterHeaderView(self)
        self._filter_header.filter_changed.connect(self.column_filter_changed.emit)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.verticalHeader().setVisible(True)
        self.verticalHeader().setDefaultSectionSize(18)
        self.setSortingEnabled(True)

        header = self.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(self._filter_header.update_positions)
        header.sectionMoved.connect(lambda *_: self._filter_header.update_positions)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.horizontalScrollBar().valueChanged.connect(
            lambda _value: self._filter_header.update_positions())

        self.viewport().installEventFilter(self)

    def configure_filters(self, columns: list[FilterColumnConfig]) -> None:
        self._filter_header.configure(columns)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self._filter_header.update_positions()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._filter_header.update_positions()
