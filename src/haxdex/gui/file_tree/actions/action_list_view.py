from io import TextIOWrapper
from pathlib import Path
import json

from loguru import logger
from PyQt6.QtCore import pyqtSignal, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QHeaderView,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.actions.action_handler import BaseAction
from haxdex.gui.file_tree.actions.action_list_model import ActionFilterProxyModel, ActionListModel
from haxdex.services.core.types import FileHash
from haxdex.services.pydantic_utils import model_to_json_data


class ActionListView(QWidget):
    file_hash_activated = pyqtSignal(object, object)

    def __init__(self,
                 actions: ActionListModel,
                 action_file: Path,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.action_file = action_file

        self.proxy_model = ActionFilterProxyModel(self)
        self.proxy_model.setSourceModel(actions)

        self.kind_filter = QLineEdit(self)
        self.kind_filter.setPlaceholderText("filter kind")
        self.kind_filter.textChanged.connect(
            lambda text: self.proxy_model.set_column_filter(ActionListModel.COL_KIND, text
                                                           ))

        self.path_filter = QLineEdit(self)
        self.path_filter.setPlaceholderText("filter path")
        self.path_filter.textChanged.connect(
            lambda text: self.proxy_model.set_column_filter(ActionListModel.COL_PATH, text
                                                           ))

        self.message_filter = QLineEdit(self)
        self.message_filter.setPlaceholderText("filter message")
        self.message_filter.textChanged.connect(
            lambda text: self.proxy_model.set_column_filter(ActionListModel.COL_MESSAGE,
                                                            text))

        self.list_view = QTableView(self)
        self.list_view.setModel(self.proxy_model)
        self.list_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.list_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.list_view.verticalHeader().setVisible(True)
        self.list_view.verticalHeader().setDefaultSectionSize(18)
        self.list_view.doubleClicked.connect(self._on_tree_item_double_clicked)
        self.list_view.setSortingEnabled(True)

        header = self.list_view.horizontalHeader()
        assert header
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list_view.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.list_view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list_view.resizeColumnsToContents()

        self.save_actions_button = QPushButton("save actions", self)
        self.save_actions_button.clicked.connect(self._on_save_actions_clicked)

        self.overwrite_actions_button = QPushButton("overwrite actions", self)
        self.overwrite_actions_button.clicked.connect(self._overwrite_actions)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filters_layout = QHBoxLayout()
        filters_layout.addWidget(self.kind_filter)
        filters_layout.addWidget(self.path_filter)
        filters_layout.addWidget(self.message_filter)
        layout.addLayout(filters_layout)

        layout.addWidget(self.list_view)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.save_actions_button)
        buttons_layout.addWidget(self.overwrite_actions_button)
        layout.addLayout(buttons_layout)

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
        model = self.list_view.model()
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
