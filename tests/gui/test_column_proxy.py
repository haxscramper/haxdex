from dataclasses import dataclass

import pandas as pd
import pytest
from beartype import beartype
from beartype.typing import Any, Sequence

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt

from haxdex.gui.agnostic.column_model import ColumnSpec, AbstractColumnItemModel
from haxdex.gui.agnostic.column_sort_filter_proxy import ColumnSortFilterProxyModel
from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.common.qt_utils import qt_model_to_dataframe


@beartype
@dataclass(frozen=True)
class RowData:
    code: str
    category: str
    amount: int


@beartype
class CodeColumn(ColumnSpec):

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        row = index.internalPointer()
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return row.code.upper()
            case CustomModelRole.SortDataRole.value:
                return row.code.lower()
            case CustomModelRole.FilterDataRole.value:
                return row.code.lower()
            case _:
                return None

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return "code"
            case _:
                return None


@beartype
class CategoryColumn(ColumnSpec):

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        row = index.internalPointer()
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return row.category.title()
            case CustomModelRole.SortDataRole.value:
                return row.category.lower()
            case CustomModelRole.FilterDataRole.value:
                return row.category.lower()
            case _:
                return None

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return "category"
            case _:
                return None


@beartype
class AmountColumn(ColumnSpec):

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        row = index.internalPointer()
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return f"{row.amount:03d}"
            case CustomModelRole.SortDataRole.value:
                return row.amount
            case CustomModelRole.FilterDataRole.value:
                return row.amount
            case _:
                return None

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return "amount"
            case _:
                return None


@beartype
class FlatRowsModel(AbstractColumnItemModel):

    def __init__(self, rows: Sequence[RowData]) -> None:
        super().__init__(columns=[CodeColumn(), CategoryColumn(), AmountColumn()])
        self.rows: tuple[RowData, ...] = tuple(rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.rows)

    def index(
            self,
            row: int,
            column: int,
            parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if parent.isValid():
            return QModelIndex()

        if row < 0 or len(self.rows) <= row:
            return QModelIndex()

        if column < 0 or self.columnCount(QModelIndex()) <= column:
            return QModelIndex()

        return self.createIndex(row, column, self.rows[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        return QModelIndex()


@beartype
def makeSourceModel() -> FlatRowsModel:
    rows = [
        RowData(code="b2", category="alpha", amount=2),
        RowData(code="a1", category="beta", amount=10),
        RowData(code="a1", category="alpha", amount=5),
        RowData(code="c3", category="beta", amount=1),
        RowData(code="b2", category="alpha", amount=1),
    ]
    return FlatRowsModel(rows)


@beartype
def collectColumn(model: QAbstractItemModel, column: int) -> list[Any]:
    values: list[Any] = []
    for row in range(model.rowCount(QModelIndex())):
        index = model.index(row, column, QModelIndex())
        values.append(model.data(index, Qt.ItemDataRole.DisplayRole))
    return values


def test_proxy_default_multi_column_sort() -> None:
    source = makeSourceModel()
    proxy = ColumnSortFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    codes = collectColumn(proxy, 0)
    categories = collectColumn(proxy, 1)
    amounts = collectColumn(proxy, 2)

    assert codes == ["A1", "A1", "B2", "B2", "C3"]
    assert categories == ["Alpha", "Beta", "Alpha", "Alpha", "Beta"]
    assert amounts == ["005", "010", "001", "002", "001"]


def test_proxy_custom_sort_priority() -> None:
    source = makeSourceModel()
    proxy = ColumnSortFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.setSortPriority([2, 0, 1])
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    codes = collectColumn(proxy, 0)
    amounts = collectColumn(proxy, 2)

    assert codes == ["B2", "C3", "B2", "A1", "A1"]
    assert amounts == ["001", "001", "002", "005", "010"]


def test_proxy_per_column_filter_rules() -> None:
    source = makeSourceModel()
    proxy = ColumnSortFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.setFilterRule(1, lambda value: value == "alpha")
    proxy.setFilterRule(2, lambda value: value <= 2)
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    codes = collectColumn(proxy, 0)
    categories = collectColumn(proxy, 1)
    amounts = collectColumn(proxy, 2)

    assert codes == ["B2", "B2"]
    assert categories == ["Alpha", "Alpha"]
    assert amounts == ["001", "002"]


def test_proxy_dataframe_formatting() -> None:
    source = makeSourceModel()
    proxy = ColumnSortFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    frame = qt_model_to_dataframe(proxy, role=int(Qt.ItemDataRole.DisplayRole))

    assert list(frame.columns) == ["code", "category", "amount"]
    assert frame["amount"].tolist() == ["005", "010", "001", "002", "001"]
    assert frame["category"].tolist() == ["Alpha", "Beta", "Alpha", "Alpha", "Beta"]


def test_proxy_dataframe_sort_and_filter() -> None:
    source = makeSourceModel()
    proxy = ColumnSortFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.setSortPriority([2, 0, 1])
    proxy.setFilterRule(1, lambda value: value == "alpha")
    proxy.setFilterRule(2, lambda value: value <= 2)
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    frame = qt_model_to_dataframe(proxy, role=int(Qt.ItemDataRole.DisplayRole))

    assert frame["code"].tolist() == ["B2", "B2"]
    assert frame["category"].tolist() == ["Alpha", "Alpha"]
    assert frame["amount"].tolist() == ["001", "002"]
