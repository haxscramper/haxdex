from __future__ import annotations

import pytest

from PyQt6.QtCore import QCoreApplication, Qt, QSortFilterProxyModel
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from beartype.typing import List, Iterator, Tuple

from haxdex.gui.agnostic.tree_to_table_model import TreeToTableProxyModel


@pytest.fixture(scope="session", autouse=True)
def qcore_application() -> Iterator[QCoreApplication]:
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def make_row(name: str, first: str, second: str) -> List[QStandardItem]:
    return [QStandardItem(name), QStandardItem(first), QStandardItem(second)]


def make_tree_model() -> QStandardItemModel:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["name", "c1", "c2"])

    item0 = make_row("item0", "a0", "b0")
    model.appendRow(item0)

    nested0 = make_row("n0", "a1", "b1")
    nested1 = make_row("n1", "a2", "b2")
    item0[0].appendRow(nested0)
    item0[0].appendRow(nested1)

    nested_nested0 = make_row("nn0", "a3", "b3")
    nested1[0].appendRow(nested_nested0)

    item1 = make_row("item1", "a4", "b4")
    model.appendRow(item1)

    return model


def flat_column0(model: object) -> List[str]:
    return [
        model.data(model.index(row, 0), Qt.ItemDataRole.DisplayRole)
        for row in range(model.rowCount())
    ]


@pytest.fixture
def tree() -> QStandardItemModel:
    return make_tree_model()


@pytest.fixture
def proxy(tree: QStandardItemModel) -> TreeToTableProxyModel:
    flatten = TreeToTableProxyModel()
    flatten.setSourceModel(tree)
    return flatten


def test_column_count(proxy: TreeToTableProxyModel) -> None:
    assert proxy.columnCount() == 3


def test_flatten_order_and_row_count(proxy: TreeToTableProxyModel) -> None:
    assert proxy.rowCount() == 5
    assert flat_column0(proxy) == ["item0", "n0", "n1", "nn0", "item1"]


def test_data_access_matches_source(proxy: TreeToTableProxyModel,
                                    tree: QStandardItemModel) -> None:
    proxy_index = proxy.index(2, 1)
    source_index = proxy.mapToSource(proxy_index)
    assert proxy.data(proxy_index) == "a2"
    assert proxy.data(proxy_index) == tree.data(source_index)


def test_header_data(proxy: TreeToTableProxyModel) -> None:
    assert proxy.headerData(1, Qt.Orientation.Horizontal) == "c1"


def test_map_roundtrip(proxy: TreeToTableProxyModel) -> None:
    for row in range(proxy.rowCount()):
        for column in range(proxy.columnCount()):
            proxy_index = proxy.index(row, column)
            source_index = proxy.mapToSource(proxy_index)
            assert source_index.isValid()
            mapped_back = proxy.mapFromSource(source_index)
            assert mapped_back.row() == row
            assert mapped_back.column() == column


def test_data_change_propagates(proxy: TreeToTableProxyModel,
                                tree: QStandardItemModel) -> None:
    emitted: List[Tuple[int, int]] = []

    def on_data_changed(top, bottom, roles=None) -> None:
        emitted.append((top.row(), top.column()))

    proxy.dataChanged.connect(on_data_changed)

    target = tree.index(0, 0, tree.index(0, 0))
    tree.setData(target, "renamed", Qt.ItemDataRole.EditRole)

    assert (1, 0) in emitted


def test_structure_rebuilds_on_insert(proxy: TreeToTableProxyModel,
                                      tree: QStandardItemModel) -> None:
    tree.appendRow(make_row("item2", "a5", "b5"))
    assert proxy.rowCount() == 6
    assert flat_column0(proxy)[-1] == "item2"


def test_unequal_columns_raises() -> None:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["a", "b", "c"])
    root = make_row("root", "y", "z")
    model.appendRow(root)
    root[0].appendRow([QStandardItem("only_one_column")])

    proxy = TreeToTableProxyModel()
    with pytest.raises(ValueError) as context:
        proxy.setSourceModel(model)

    assert "columns" in str(context.value)


def test_column_children_raises() -> None:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["a", "b", "c"])
    root = make_row("root", "y", "z")
    model.appendRow(root)
    root[1].appendRow(make_row("bad_nested", "1", "2"))

    proxy = TreeToTableProxyModel()
    with pytest.raises(ValueError) as context:
        proxy.setSourceModel(model)

    assert "only column 0" in str(context.value)


@pytest.fixture
def top(tree: QStandardItemModel) -> QSortFilterProxyModel:
    flatten = TreeToTableProxyModel()
    flatten.setSourceModel(tree)

    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(flatten)
    return proxy


def test_sort_descending(top: QSortFilterProxyModel) -> None:
    top.sort(0, Qt.SortOrder.DescendingOrder)
    assert flat_column0(top) == ["nn0", "n1", "n0", "item1", "item0"]


def test_sort_ascending(top: QSortFilterProxyModel) -> None:
    top.sort(0, Qt.SortOrder.AscendingOrder)
    assert flat_column0(top) == ["item0", "item1", "n0", "n1", "nn0"]


def test_filter(top: QSortFilterProxyModel) -> None:
    top.setFilterKeyColumn(0)
    top.setFilterFixedString("item")
    assert sorted(flat_column0(top)) == ["item0", "item1"]


def test_recursive_filter_then_flatten() -> None:
    tree = make_tree_model()
    pre = QSortFilterProxyModel()
    pre.setRecursiveFilteringEnabled(True)
    pre.setFilterKeyColumn(0)
    pre.setSourceModel(tree)
    pre.setFilterFixedString("nn0")

    flatten = TreeToTableProxyModel()
    flatten.setSourceModel(pre)

    assert flat_column0(flatten) == ["item0", "n1", "nn0"]


def test_filter_change_updates_flatten() -> None:
    tree = make_tree_model()
    pre = QSortFilterProxyModel()
    pre.setRecursiveFilteringEnabled(True)
    pre.setFilterKeyColumn(0)
    pre.setSourceModel(tree)

    flatten = TreeToTableProxyModel()
    flatten.setSourceModel(pre)
    assert flatten.rowCount() == 5

    pre.setFilterFixedString("n0")
    assert flat_column0(flatten) == ["item0", "n0"]
