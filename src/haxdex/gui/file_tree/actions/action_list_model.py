import json
from pathlib import Path

from PyQt6.QtCore import QAbstractListModel, QObject, QModelIndex, Qt, QSortFilterProxyModel, QAbstractTableModel
from beartype import beartype
from pydantic import BaseModel

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.actions.action_handler import BaseAction, ActionHandler
from haxdex.gui.file_tree.actions.action_move_file import MoveAction
from haxdex.gui.file_tree.actions.action_trash_file import TrashAction
from haxdex.gui.file_tree.actions.action_video_convert import VideoConvertAction
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode
from haxdex.gui.file_tree.columns.video_convert_column import VideoConvertData
from haxdex.services.pydantic_utils import model_from_json_data


def to_action_handler_map(handlers: list[ActionHandler]) -> dict[str, ActionHandler]:
    types: dict[str, ActionHandler] = dict()
    for handler in handlers:
        types[handler.action_type.kind] = handler

    return types


def to_action_types_map(handlers: list[ActionHandler]) -> dict[str, type[BaseAction]]:
    types: dict[str, type[BaseAction]] = dict()
    for handler in handlers:
        types[handler.action_type.kind] = handler.action_type

    return types


def load_actions(jsonl_path: Path, handlers: list[ActionHandler]) -> list[BaseAction]:
    actions: list[BaseAction] = []
    types: dict[str, type[BaseAction]] = to_action_types_map(handlers)

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            actions.append(model_from_json_data(data, types[data["kind"]]))

    return actions


@beartype
class ActionProvider:

    def __init__(self) -> None:
        self.actions: list[BaseModel] = []

    def trash(self, file: FileTreeNode, message: str | None = None) -> None:
        self.actions.append(TrashAction(file=file, message=message))

    def move(self, file: FileTreeNode, dest: str, message: str | None = None) -> None:
        self.actions.append(MoveAction(file=file, dest=dest, message=message))

    def convert_video(self,
                      file: FileTreeNode,
                      target: VideoConvertData,
                      message: str | None = None):
        self.actions.append(VideoConvertAction(file=file, target=target, message=message))


@beartype
class ActionListModel(QAbstractTableModel):
    COL_KIND = 0
    COL_PATH = 1
    COL_MESSAGE = 2
    _HEADERS = ("kind", "path", "message")

    def __init__(self, actions: list[BaseAction], parent=None) -> None:
        super().__init__(parent)
        self._actions = actions

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._actions)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 3

    def headerData(self,
                   section: int,
                   orientation: Qt.Orientation,
                   role: int = int(Qt.ItemDataRole.DisplayRole)):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._HEADERS):
                return self._HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid():
            return None

        action = self._actions[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.COL_KIND:
                return action.kind
            if col == self.COL_PATH:
                return str(action.file.path)
            if col == self.COL_MESSAGE:
                return action.message or ""
            return None

        if role == CustomModelRole.HashRole.value:
            return action.file.hash.hash

        if role == CustomModelRole.ActionRole.value:
            return action

        return None

    def roleNames(self) -> dict[int, bytes]:
        names = super().roleNames()
        names[CustomModelRole.ActionRole.value] = b"action"  # type: ignore
        names[CustomModelRole.HashRole.value] = b"hash"  # type: ignore
        return names  # type: ignore

    def actions(self) -> list[BaseAction]:
        return self._actions


class ActionFilterProxyModel(QSortFilterProxyModel):

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._column_filters: dict[int, str] = {}
        self.setDynamicSortFilter(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_column_filter(self, column: int, text: str) -> None:
        text = text.strip()
        if text:
            self._column_filters[column] = text
        else:
            self._column_filters.pop(column, None)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        assert model is not None

        for column, needle in self._column_filters.items():
            idx = model.index(source_row, column, source_parent)
            value = idx.data(Qt.ItemDataRole.DisplayRole)
            haystack = "" if value is None else str(value)
            if needle.casefold() not in haystack.casefold():
                return False

        return True
