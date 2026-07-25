from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractProxyModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

from beartype.typing import Optional, List, Dict, Tuple


class TreeToTableProxyModel(QAbstractProxyModel):

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.flat: List[QPersistentModelIndex] = []
        self.positions: Dict[QPersistentModelIndex, int] = {}
        self.connections: List[Tuple[object, object]] = []

    def setSourceModel(self, model: Optional[QObject]) -> None:
        for signal, slot in self.connections:
            signal.disconnect(slot)
        self.connections = []

        self.beginResetModel()
        super().setSourceModel(model)
        if model is not None:
            for signal in (
                    model.modelReset,
                    model.layoutChanged,
                    model.rowsInserted,
                    model.rowsRemoved,
                    model.rowsMoved,
                    model.columnsInserted,
                    model.columnsRemoved,
            ):
                signal.connect(self._on_source_structure_changed)
                self.connections.append((signal, self._on_source_structure_changed))
            model.dataChanged.connect(self._on_source_data_changed)
            self.connections.append((model.dataChanged, self._on_source_data_changed))
        self._rebuild()
        self.endResetModel()

    def _on_source_structure_changed(self, *args: object) -> None:
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    def _on_source_data_changed(
        self,
        top_left: QModelIndex,
        bottom_right: QModelIndex,
        roles: Optional[List[int]] = None,
    ) -> None:
        source = self.sourceModel()
        parent = top_left.parent()
        for row in range(top_left.row(), bottom_right.row() + 1):
            for column in range(top_left.column(), bottom_right.column() + 1):
                proxy_index = self.mapFromSource(source.index(row, column, parent))
                if proxy_index.isValid():
                    self.dataChanged.emit(proxy_index, proxy_index, roles or [])

    def _verify_structure(self) -> None:
        source = self.sourceModel()
        root_columns = source.columnCount(QModelIndex())

        def check(parent: QModelIndex, parent_description: str) -> None:
            rows = source.rowCount(parent)
            columns = source.columnCount(parent)
            if 0 < rows and columns != root_columns:
                raise ValueError(
                    f"Parent {parent_description} exposes {columns} columns "
                    f"but the root exposes {root_columns} columns; every level "
                    f"must expose the same number of columns to be flattened")
            for row in range(rows):
                for column in range(columns):
                    cell = source.index(row, column, parent)
                    if column != 0 and source.hasChildren(cell):
                        raise ValueError(
                            f"Cell at row {row} column {column} under "
                            f"{parent_description} has nested rows; only column 0 "
                            f"is allowed to carry nested rows")
                check(
                    source.index(row, 0, parent),
                    f"row {row} under {parent_description}",
                )

        check(QModelIndex(), "<root>")

    def _rebuild(self) -> None:
        self.flat = []
        self.positions = {}
        source = self.sourceModel()
        if source is None:
            return
        self._verify_structure()

        def walk(parent: QModelIndex) -> None:
            for row in range(source.rowCount(parent)):
                node = source.index(row, 0, parent)
                persistent = QPersistentModelIndex(node)
                self.positions[persistent] = len(self.flat)
                self.flat.append(persistent)
                walk(node)

        walk(QModelIndex())

    def mapToSource(self, proxyIndex: QModelIndex) -> QModelIndex:
        if not proxyIndex.isValid():
            return QModelIndex()
        node = self.flat[proxyIndex.row()]
        source = self.sourceModel()
        return source.index(node.row(), proxyIndex.column(), node.parent())

    def mapFromSource(self, sourceIndex: QModelIndex) -> QModelIndex:
        if not sourceIndex.isValid():
            return QModelIndex()
        column_zero = sourceIndex.sibling(sourceIndex.row(), 0)
        key = QPersistentModelIndex(column_zero)
        if key not in self.positions:
            return QModelIndex()
        return self.createIndex(self.positions[key], sourceIndex.column())

    def index(
            self,
            row: int,
            column: int,
            parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if parent.isValid():
            return QModelIndex()
        if row < 0 or len(self.flat) <= row:
            return QModelIndex()
        if column < 0 or self.columnCount() <= column:
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:
        return QModelIndex()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.flat)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        source = self.sourceModel()
        if source is None:
            return 0
        return source.columnCount(QModelIndex())

    def data(
        self,
        proxyIndex: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not proxyIndex.isValid():
            return None
        return self.sourceModel().data(self.mapToSource(proxyIndex), role)

    def setData(
        self,
        proxyIndex: QModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not proxyIndex.isValid():
            return False
        return self.sourceModel().setData(self.mapToSource(proxyIndex), value, role)

    def flags(self, proxyIndex: QModelIndex) -> Qt.ItemFlag:
        if not proxyIndex.isValid():
            return Qt.ItemFlag.NoItemFlags
        return self.sourceModel().flags(self.mapToSource(proxyIndex))

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        source = self.sourceModel()
        if source is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return source.headerData(section, orientation, role)
        return super().headerData(section, orientation, role)
