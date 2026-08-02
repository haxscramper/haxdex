import json
from pathlib import Path

from PyQt6.QtCore import QAbstractListModel, QObject, QModelIndex, Qt, QSortFilterProxyModel, QAbstractTableModel
from beartype import beartype
from beartype.typing import Any
from pydantic import BaseModel

from haxdex.gui.agnostic.column_model import AbstractColumnItemModel, ColumnSpec
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
class ActionKindColumn(ColumnSpec):

    def data(
            self,
            index: QModelIndex,
            role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        action = index.internalPointer()
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return action.kind
            case CustomModelRole.SortDataRole.value:
                return action.kind.casefold()
            case CustomModelRole.FilterDataRole.value:
                return action.kind.casefold()
            case CustomModelRole.HashRole.value:
                return action.file.hash.hash
            case CustomModelRole.ActionRole.value:
                return action
            case _:
                return None

    def setData(
            self,
            index: QModelIndex,
            value: Any,
            role: int = int(Qt.ItemDataRole.EditRole),
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return "kind"
            case _:
                return None


@beartype
class ActionPathColumn(ColumnSpec):

    def data(
            self,
            index: QModelIndex,
            role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        action = index.internalPointer()
        pathText = str(action.file.path)
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return pathText
            case CustomModelRole.SortDataRole.value:
                return pathText.casefold()
            case CustomModelRole.FilterDataRole.value:
                return pathText.casefold()
            case CustomModelRole.HashRole.value:
                return action.file.hash.hash
            case CustomModelRole.ActionRole.value:
                return action
            case _:
                return None

    def setData(
            self,
            index: QModelIndex,
            value: Any,
            role: int = int(Qt.ItemDataRole.EditRole),
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return "path"
            case _:
                return None


@beartype
class ActionMessageColumn(ColumnSpec):

    def data(
            self,
            index: QModelIndex,
            role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        action = index.internalPointer()
        messageText = action.message or ""
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return messageText
            case CustomModelRole.SortDataRole.value:
                return messageText.casefold()
            case CustomModelRole.FilterDataRole.value:
                return messageText.casefold()
            case CustomModelRole.HashRole.value:
                return action.file.hash.hash
            case CustomModelRole.ActionRole.value:
                return action
            case _:
                return None

    def setData(
            self,
            index: QModelIndex,
            value: Any,
            role: int = int(Qt.ItemDataRole.EditRole),
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return "message"
            case _:
                return None


@beartype
class ActionListModel(AbstractColumnItemModel):

    def __init__(self, actions: list[BaseAction], parent: Any = None) -> None:
        super().__init__(
            columns=[ActionKindColumn(),
                     ActionPathColumn(),
                     ActionMessageColumn()],
            parent=parent,
        )
        self.actionsList: list[BaseAction] = actions

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.actionsList)

    def index(
            self,
            row: int,
            column: int,
            parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if parent.isValid():
            return QModelIndex()

        if row < 0 or len(self.actionsList) <= row:
            return QModelIndex()

        if column < 0 or self.columnCount(QModelIndex()) <= column:
            return QModelIndex()

        return self.createIndex(row, column, self.actionsList[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        return QModelIndex()

    def roleNames(self) -> dict[int, bytes]:
        names = super().roleNames()
        names[CustomModelRole.ActionRole.value] = b"action"  # type: ignore[index]
        names[CustomModelRole.HashRole.value] = b"hash"  # type: ignore[index]
        return names  # type: ignore[return-value]

    def actions(self) -> list[BaseAction]:
        return self.actionsList
