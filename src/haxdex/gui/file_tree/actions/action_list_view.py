import json
from io import TextIOWrapper
from pathlib import Path

from beartype import beartype
from beartype.typing import Any, Callable
from loguru import logger
from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from haxdex.gui.agnostic.column_sort_filter_proxy import ColumnSortFilterProxyModel
from haxdex.gui.agnostic.filterable_table_view import FilterableTableView, FilterColumnConfig
from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.actions.action_handler import BaseAction
from haxdex.gui.file_tree.actions.action_list_model import ActionListModel
from haxdex.services.core.types import FileHash
from haxdex.services.pydantic_utils import model_to_json_data


@beartype
class FilterableTableWidget(QWidget):
    column_filter_changed = pyqtSignal(int, str)
    row_double_clicked = pyqtSignal(QModelIndex)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.table = FilterableTableView(self)
        self.table.column_filter_changed.connect(self.column_filter_changed.emit)
        self.table.doubleClicked.connect(self.row_double_clicked.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)

    def set_model(self, model: object) -> None:
        self.table.setModel(model)
        self.table.resizeColumnsToContents()

    def configure_filters(self, columns: list[FilterColumnConfig]) -> None:
        self.table.configure_filters(columns)


@beartype
class ActionListView(QWidget):
    file_hash_activated = pyqtSignal(object, object)

    def __init__(
        self,
        actions: ActionListModel,
        action_file: Path,
        parent: QWidget | None = None,
        filter_columns: list[FilterColumnConfig] | None = None,
    ) -> None:
        super().__init__(parent)

        self.actionFile = action_file
        self.columnFilters: dict[int, str] = {}

        self.proxyModel = ColumnSortFilterProxyModel(self)
        self.proxyModel.setSourceModel(actions)
        self.proxyModel.setDynamicSortFilter(True)
        self.proxyModel.sort(0, Qt.SortOrder.AscendingOrder)

        self.tableWidget = FilterableTableWidget(self)
        self.tableWidget.set_model(self.proxyModel)
        self.tableWidget.column_filter_changed.connect(self.on_column_filter_changed)
        self.tableWidget.row_double_clicked.connect(self.on_tree_item_double_clicked)

        default_filters = [
            FilterColumnConfig(0, "filter kind"),
            FilterColumnConfig(1, "filter path"),
            FilterColumnConfig(2, "filter message"),
        ]
        self.tableWidget.configure_filters(filter_columns or default_filters)

        self.saveActionsButton = QPushButton("save actions", self)
        self.saveActionsButton.clicked.connect(self.on_save_actions_clicked)

        self.overwriteActionsButton = QPushButton("overwrite actions", self)
        self.overwriteActionsButton.clicked.connect(self.overwrite_actions)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tableWidget)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.saveActionsButton)
        buttons_layout.addWidget(self.overwriteActionsButton)
        layout.addLayout(buttons_layout)

    def on_column_filter_changed(self, column: int, text: str) -> None:
        needle = text.strip().casefold()
        self.columnFilters[column] = needle
        if needle == "":
            self.proxyModel.setFilterRule(column, None)
            return

        rule: Callable[
            [Any], bool] = lambda value, search=needle: search in str(value).casefold()
        self.proxyModel.setFilterRule(column, rule)

    def on_tree_item_double_clicked(self, index: QModelIndex) -> None:
        hash_value = index.data(CustomModelRole.HashRole.value)
        if hash_value is not None:
            assert isinstance(hash_value, str), type(hash_value)
            self.file_hash_activated.emit(FileHash(hash=hash_value), None)

    @staticmethod
    def write_action(action: BaseAction, out: TextIOWrapper) -> None:
        json_data = model_to_json_data(action)
        json_data["kind"] = action.kind
        out.write(json.dumps(json_data, ensure_ascii=False))
        out.write("\n")

    def write_actions(self, out: TextIOWrapper) -> None:
        model = self.tableWidget.table.model()
        assert model is not None

        for row in range(model.rowCount()):
            index = model.index(row, 0)
            action = index.data(CustomModelRole.ActionRole.value)
            if action is None:
                continue

            assert isinstance(action, BaseAction), type(action)
            self.write_action(action, out)

    def overwrite_actions(self) -> None:
        with self.actionFile.open("w", encoding="utf-8") as out:
            self.write_actions(out)
        logger.info(f"Saved actions to {self.actionFile}")

    def on_save_actions_clicked(self) -> None:
        with self.actionFile.open("a", encoding="utf-8") as out:
            self.write_actions(out)
        logger.info(f"Saved actions to {self.actionFile}")
