import json
from pathlib import Path

from beartype import beartype
from beartype.typing import Any
from loguru import logger
from PyQt6.QtCore import QPoint, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from haxdex.gui.agnostic.column_sort_filter_proxy import ColumnSortFilterProxyModel
from haxdex.gui.agnostic.filterable_table_view import FilterColumnConfig, FilterableTableView
from haxdex.gui.agnostic.column_model import AbstractColumnItemModel
from haxdex.gui.agnostic.tree_to_table_model import TreeToTableProxyModel
from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.common.qt_utils import get_settings
from haxdex.gui.file_tree.actions.action_handler import ActionResult
from haxdex.gui.file_tree.actions.action_video_convert import VideoConvertAction, VideoConvertActionHandler
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode
from haxdex.gui.file_tree.columns.known_actions_column import KnownActionColumnSpec, KnownActionData
from haxdex.gui.file_tree.python_code_editor import PythonQueryEditor
from haxdex.gui.file_tree.query_filter import QueryFilterEvaluator, QueryResultModel
from haxdex.services.core.types import FileHash


@beartype
class QueryPickerPopup(QDialog):
    query_selected = pyqtSignal(str, str)

    def __init__(self, queries: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setWindowTitle("Select query")
        self.queries = queries

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search queries...")

        self.listWidget = QListWidget(self)
        self.listWidget.itemClicked.connect(self.on_item_clicked)
        self.listWidget.itemActivated.connect(self.on_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.listWidget)

        self.search.textChanged.connect(self.rebuild_list)
        self.search.returnPressed.connect(self.activate_current)

        self.rebuild_list()
        self.resize(320, 360)
        self.search.setFocus()

    def rebuild_list(self) -> None:
        filter_text = self.search.text().strip().lower()
        self.listWidget.clear()

        for name in sorted(self.queries, key=str.casefold):
            if filter_text and filter_text not in name.lower():
                continue
            self.listWidget.addItem(QListWidgetItem(name))

        if 0 < self.listWidget.count():
            self.listWidget.setCurrentRow(0)

    def activate_current(self) -> None:
        item = self.listWidget.currentItem()
        if item is not None:
            self.on_item_clicked(item)

    def on_item_clicked(self, item: QListWidgetItem) -> None:
        name = item.text()
        self.query_selected.emit(name, self.queries[name])
        self.close()


@beartype
class TreeModelViewer(QWidget):
    row_double_clicked = pyqtSignal(QModelIndex)

    def __init__(
        self,
        model: AbstractColumnItemModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = model

        self.treeView = QTreeView(self)
        self.treeView.setIndentation(20)
        self.treeView.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.treeView.setEditTriggers(QAbstractItemView.EditTrigger.CurrentChanged)
        self.treeView.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.treeView.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.treeView.setModel(self.model)
        self.model.configureView(self.treeView)
        self.treeView.doubleClicked.connect(self.row_double_clicked.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.treeView)

    def current_model(self) -> Any:
        return self.model

    def selected_nodes(self) -> list[FileTreeNode]:
        selection = self.treeView.selectionModel()
        nodes: list[FileTreeNode] = []
        for index in selection.selectedRows():
            node = index.internalPointer()
            if node is not None:
                nodes.append(node)
        return nodes


@beartype
class TableModelViewer(QWidget):
    row_double_clicked = pyqtSignal(QModelIndex)

    def __init__(
        self,
        model: AbstractColumnItemModel,
        columns: list[FileTreeColumnSpec],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.sourceModel = model
        self.columns = columns

        self.tableModel = TreeToTableProxyModel(self)
        self.tableModel.setSourceModel(self.sourceModel)

        self.sortFilterModel = ColumnSortFilterProxyModel(self)
        self.sortFilterModel.setSourceModel(self.tableModel)
        self.sortPriority: list[int] = list(
            range(self.tableModel.columnCount(QModelIndex())))
        self.sortOrderByColumn: dict[int, Qt.SortOrder] = {}
        self.sortFilterModel.setSortPriority(self.sortPriority)
        self.sortFilterModel.sort(0, Qt.SortOrder.AscendingOrder)

        self.tableView = FilterableTableView(self)
        self.tableView.setModel(self.sortFilterModel)
        self.tableView.setEditTriggers(QAbstractItemView.EditTrigger.CurrentChanged)
        self.tableView.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tableView.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tableView.doubleClicked.connect(self.row_double_clicked.emit)
        self.tableView.configure_filters(self.build_filter_columns())
        self.tableView.resizeColumnsToContents()
        model.configureView(self.tableView)

        header = self.tableView.horizontalHeader()
        assert header is not None
        header.sectionClicked.connect(self.on_header_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tableView)

    def build_filter_columns(self) -> list[FilterColumnConfig]:
        result: list[FilterColumnConfig] = []
        columnCount = self.sourceModel.columnCount(QModelIndex())
        for column in range(columnCount):
            header = self.sourceModel.headerData(
                column,
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.DisplayRole,
            )
            placeholder = f"filter {header}" if header is not None else f"filter {column}"
            result.append(FilterColumnConfig(column=column, placeholder=placeholder))
        return result

    def on_header_clicked(self, column: int) -> None:
        if column in self.sortPriority:
            self.sortPriority.remove(column)
        self.sortPriority.insert(0, column)

        columnCount = self.tableModel.columnCount(QModelIndex())
        for idx in range(columnCount):
            if idx not in self.sortPriority:
                self.sortPriority.append(idx)

        previousOrder = self.sortOrderByColumn.get(column, Qt.SortOrder.DescendingOrder)
        if previousOrder == Qt.SortOrder.AscendingOrder:
            order = Qt.SortOrder.DescendingOrder
        else:
            order = Qt.SortOrder.AscendingOrder

        self.sortOrderByColumn[column] = order
        self.sortFilterModel.setSortPriority(self.sortPriority)
        self.sortFilterModel.sort(column, order)

        header = self.tableView.horizontalHeader()
        assert header is not None
        header.setSortIndicator(column, order)

    def current_model(self) -> object:
        return self.sortFilterModel

    def selected_nodes(self) -> list[FileTreeNode]:
        selection = self.tableView.selectionModel()
        nodes: list[FileTreeNode] = []
        for proxy_index in selection.selectedRows():
            mapped = self.sortFilterModel.mapToSource(proxy_index)
            source_index = self.tableModel.mapToSource(mapped)
            node = source_index.internalPointer()
            if node is not None:
                nodes.append(node)
        return nodes


@beartype
class SwitchableModelViewer(QWidget):
    row_double_clicked = pyqtSignal(QModelIndex)

    def __init__(
        self,
        model: AbstractColumnItemModel,
        columns: list[FileTreeColumnSpec],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.treeViewer = TreeModelViewer(model=model, parent=self)
        self.tableViewer = TableModelViewer(model=model, columns=columns, parent=self)

        self.modeButton = QToolButton(self)
        self.modeButton.setText("Table")
        self.modeButton.setCheckable(True)
        self.modeButton.toggled.connect(self.on_mode_toggled)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.treeViewer)
        self.stack.addWidget(self.tableViewer)
        self.stack.setCurrentWidget(self.treeViewer)

        self.treeViewer.row_double_clicked.connect(self.row_double_clicked.emit)
        self.tableViewer.row_double_clicked.connect(self.row_double_clicked.emit)

        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addWidget(self.modeButton)
        toolbar_layout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbar)
        layout.addWidget(self.stack)

    def on_mode_toggled(self, checked: bool) -> None:
        match checked:
            case True:
                self.stack.setCurrentWidget(self.tableViewer)
                self.modeButton.setText("Tree")
            case False:
                self.stack.setCurrentWidget(self.treeViewer)
                self.modeButton.setText("Table")

    def current_model(self) -> Any:
        current = self.stack.currentWidget()
        match current:
            case TreeModelViewer():
                return current.current_model()
            case TableModelViewer():
                return current.current_model()
            case _:
                raise TypeError(f"Unsupported viewer type {type(current)}")

    def selected_nodes(self) -> list[FileTreeNode]:
        current = self.stack.currentWidget()
        match current:
            case TreeModelViewer():
                return current.selected_nodes()
            case TableModelViewer():
                return current.selected_nodes()
            case _:
                raise TypeError(f"Unsupported viewer type {type(current)}")


@beartype
class FileTreeRegion(QWidget):
    query_submitted = pyqtSignal(object)
    named_queries_changed = pyqtSignal()
    file_hash_activated = pyqtSignal(object, object)

    def __init__(
        self,
        model: AbstractColumnItemModel,
        columns: list[FileTreeColumnSpec],
        region_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.columns = columns
        self.model = model
        self.regionId = region_id
        self.queryPicker: QueryPickerPopup | None = None

        self.modelViewer = SwitchableModelViewer(model=self.model,
                                                 columns=self.columns,
                                                 parent=self)
        self.modelViewer.row_double_clicked.connect(self.on_item_double_clicked)

        self.queryEdit = PythonQueryEditor(self)

        self.selectQueryButton = QToolButton(self)
        self.selectQueryButton.setText("☰")
        self.selectQueryButton.setToolTip("Select saved query")
        self.selectQueryButton.clicked.connect(self.show_query_picker)

        self.queryNameEdit = QLineEdit(self)
        self.queryNameEdit.setPlaceholderText("Query name")

        self.newQueryButton = QPushButton("New", self)
        self.newQueryButton.clicked.connect(self.new_query)

        self.saveQueryButton = QPushButton("Save", self)
        self.saveQueryButton.clicked.connect(self.save_named_query)

        query_toolbar = QWidget(self)
        query_toolbar_layout = QHBoxLayout(query_toolbar)
        query_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        query_toolbar_layout.setSpacing(8)
        query_toolbar_layout.addWidget(self.selectQueryButton)
        query_toolbar_layout.addWidget(self.queryNameEdit, 1)
        query_toolbar_layout.addWidget(self.newQueryButton)
        query_toolbar_layout.addWidget(self.saveQueryButton)

        self.runButton = QPushButton("Filter →", self)
        self.runButton.clicked.connect(self.on_run)

        submit = QShortcut(QKeySequence("Ctrl+Return"), self.queryEdit)
        submit.activated.connect(self.on_run)

        bottom = QWidget(self)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(query_toolbar)
        bottom_layout.addWidget(self.queryEdit)
        bottom_layout.addWidget(self.runButton)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.addWidget(self.modelViewer)
        self.splitter.addWidget(bottom)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

        self.queryNameEdit.textChanged.connect(self.persist_current_query_state)
        if hasattr(self.queryEdit, "textChanged"):
            self.queryEdit.textChanged.connect(self.persist_current_query_state)

        self.load_current_query_state()

    @staticmethod
    def read_named_queries() -> dict[str, str]:
        serialized = get_settings().value("queries/named", "{}")
        queries = json.loads(str(serialized))
        if not isinstance(queries, dict):
            raise ValueError("queries/named must contain a JSON object")
        return {str(name): str(query) for name, query in queries.items()}

    @staticmethod
    def write_named_queries(queries: dict[str, str]) -> None:
        get_settings().setValue("queries/named", json.dumps(queries, sort_keys=True))

    def current_query_settings_key(self) -> str:
        return f"queries/current/{self.regionId}"

    def read_current_query_state(self) -> dict[str, str]:
        serialized = get_settings().value(self.current_query_settings_key(), "{}")
        state = json.loads(str(serialized))
        if not isinstance(state, dict):
            raise ValueError(
                f"{self.current_query_settings_key()} must contain a JSON object")
        return {
            "name": str(state.get("name", "")),
            "text": str(state.get("text", "")),
        }

    def write_current_query_state(self, name: str, text: str) -> None:
        get_settings().setValue(
            self.current_query_settings_key(),
            json.dumps({
                "name": name,
                "text": text
            }, sort_keys=True),
        )

    def persist_current_query_state(self, *args: Any) -> None:
        self.write_current_query_state(self.queryNameEdit.text(), self.queryEdit.text())

    def load_current_query_state(self) -> None:
        state = self.read_current_query_state()
        self.queryNameEdit.setText(state["name"])
        self.queryEdit.setText(state["text"])

    def on_item_double_clicked(self, index: QModelIndex) -> None:
        column = index.data(CustomModelRole.ColumnSpecRole.value)
        hash_value = index.data(CustomModelRole.HashRole.value)

        if column is not None and isinstance(column, KnownActionColumnSpec):
            data = index.data(CustomModelRole.FullDataRole.value)
            logger.warning(f"full data for known actions is none column {index.column()}")
            if data is None:
                return

            class QueryActionResult(ActionResult):

                def __init__(self) -> None:
                    self.paths: list[Path] = []

                def getResultPaths(self) -> list[Path]:
                    return self.paths

            assert isinstance(data, KnownActionData)
            result = QueryActionResult()
            for act in data.actions:
                match act:
                    case VideoConvertAction():
                        assert column.executor
                        handler = column.executor.handlers[
                            VideoConvertActionHandler.action_type.kind]
                        assert isinstance(handler, VideoConvertActionHandler)
                        result.paths.append(handler.dest_path(act))
                    case _:
                        continue

            logger.info(f"file hash activated {result.paths}")
            self.file_hash_activated.emit(FileHash(hash=hash_value), result)
            return

        if hash_value is None:
            logger.info("hash value is None")
            return

        self.file_hash_activated.emit(FileHash(hash=hash_value), None)

    def refresh_named_queries(self) -> None:
        return

    def show_query_picker(self, checked: bool = False) -> None:
        queries = self.read_named_queries()
        self.queryPicker = QueryPickerPopup(queries, self)
        self.queryPicker.query_selected.connect(self.load_named_query)

        button_pos = self.selectQueryButton.mapToGlobal(
            QPoint(0, self.selectQueryButton.height()))
        self.queryPicker.move(button_pos)
        self.queryPicker.show()

    def load_named_query(self, name: str, query: str) -> None:
        self.queryNameEdit.setText(name)
        self.queryEdit.setText(query)
        self.queryEdit.setFocus()
        self.persist_current_query_state()

    def new_query(self, checked: bool = False) -> None:
        self.queryNameEdit.clear()
        self.queryEdit.setText("")
        self.queryEdit.setFocus()
        self.persist_current_query_state()

    def save_named_query(self, checked: bool = False) -> None:
        name = self.queryNameEdit.text().strip()
        if not name:
            return

        queries = self.read_named_queries()
        queries[name] = self.queryEdit.text()
        self.write_named_queries(queries)
        self.named_queries_changed.emit()
        self.persist_current_query_state()

    def query_text(self) -> str:
        return self.queryEdit.text()

    def selected_nodes(self) -> list[FileTreeNode]:
        return self.modelViewer.selected_nodes()

    def compute_filtered(self) -> QueryResultModel:
        text = self.query_text()
        selected = self.selected_nodes()
        scope = selected if selected else None
        evaluator = QueryFilterEvaluator()
        return evaluator.filter_model(
            self.modelViewer.current_model(),
            text,
            scope_nodes=scope,
        )

    def on_run(self, checked: bool = False) -> None:
        logger.info("run clicked")
        self.query_submitted.emit(self)
