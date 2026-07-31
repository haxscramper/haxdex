import json
from pathlib import Path

from PyQt6.QtCore import QAbstractListModel, QObject, QModelIndex, Qt
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

    def trash(self, file: FileTreeNode) -> None:
        self.actions.append(TrashAction(file=file))

    def move(self, file: FileTreeNode, dest: str) -> None:
        self.actions.append(MoveAction(file=file, dest=dest))

    def convert_video(self, file: FileTreeNode, target: VideoConvertData):
        self.actions.append(VideoConvertAction(file=file, target=target))


@beartype
class ActionListModel(QAbstractListModel):

    def __init__(self, actions: list[BaseAction], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._actions = actions

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._actions)

    def data(self, index: QModelIndex,
             role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid():
            return None

        action = self._actions[index.row()]

        match role:
            case Qt.ItemDataRole.DisplayRole:
                match action:
                    case TrashAction():
                        return f"trash {action.file.path}"

                    case _:
                        return str(action)

            case CustomModelRole.HashRole.value:
                return action.file.hash.hash

            case CustomModelRole.ActionRole.value:
                return action

        return None

    def roleNames(self) -> dict[int, bytes]:
        names = super().roleNames()
        names[self.ActionRole] = b"action"  # type: ignore
        return names  # type: ignore

    def actions(self) -> list[BaseAction]:
        return self._actions
