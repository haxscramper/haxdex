from io import TextIOWrapper
from pathlib import Path
import json

from loguru import logger
from PyQt6.QtCore import QModelIndex, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from haxdex.gui.agnostic.filterable_table_view import FilterableTableView, FilterColumnConfig
from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.actions.action_handler import BaseAction
from haxdex.gui.file_tree.actions.action_list_model import ActionFilterProxyModel, ActionListModel
from haxdex.services.core.types import FileHash
from haxdex.services.pydantic_utils import model_to_json_data


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

    def set_model(self, model) -> None:
        self.table.setModel(model)
        self.table.resizeColumnsToContents()

    def configure_filters(self, columns: list[FilterColumnConfig]) -> None:
        self.table.configure_filters(columns)


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

        self.action_file = action_file

        self.proxy_model = ActionFilterProxyModel(self)
        self.proxy_model.setSourceModel(actions)

        self.table_widget = FilterableTableWidget(self)
        self.table_widget.set_model(self.proxy_model)
        self.table_widget.column_filter_changed.connect(self._on_column_filter_changed)
        self.table_widget.row_double_clicked.connect(self._on_tree_item_double_clicked)

        default_filters = [
            FilterColumnConfig(ActionListModel.COL_KIND, "filter kind"),
            FilterColumnConfig(ActionListModel.COL_PATH, "filter path"),
            FilterColumnConfig(ActionListModel.COL_MESSAGE, "filter message"),
        ]
        self.table_widget.configure_filters(filter_columns or default_filters)

        self.save_actions_button = QPushButton("save actions", self)
        self.save_actions_button.clicked.connect(self._on_save_actions_clicked)

        self.overwrite_actions_button = QPushButton("overwrite actions", self)
        self.overwrite_actions_button.clicked.connect(self._overwrite_actions)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table_widget)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.save_actions_button)
        buttons_layout.addWidget(self.overwrite_actions_button)
        layout.addLayout(buttons_layout)

    def _on_column_filter_changed(self, column: int, text: str) -> None:
        self.proxy_model.set_column_filter(column, text)

    def _on_tree_item_double_clicked(self, index: QModelIndex) -> None:
        hash_value = index.data(CustomModelRole.HashRole.value)
        if hash_value is not None:
            assert isinstance(hash_value, str), type(hash_value)
            self.file_hash_activated.emit(FileHash(hash=hash_value), None)

    @staticmethod
    def _write_action1(action: BaseAction, out: TextIOWrapper):
        json_data = model_to_json_data(action)
        json_data["kind"] = action.kind
        out.write(json.dumps(json_data, ensure_ascii=False))
        out.write("\n")

    def _write_actions(self, out: TextIOWrapper):
        model = self.table_widget.table.model()
        assert model is not None

        for row in range(model.rowCount()):
            index = model.index(row, 0)
            action = index.data(CustomModelRole.ActionRole.value)
            if action is None:
                continue

            assert isinstance(action, BaseAction), type(action)
            self._write_action1(action, out)

    def _overwrite_actions(self) -> None:
        with self.action_file.open("w", encoding="utf-8") as out:
            self._write_actions(out)

        logger.info(f"Saved actions to {self.action_file}")

    def _on_save_actions_clicked(self) -> None:
        with self.action_file.open("a", encoding="utf-8") as out:
            self._write_actions(out)

        logger.info(f"Saved actions to {self.action_file}")
