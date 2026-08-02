from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from beartype import beartype
from beartype.typing import Any
from pydantic import BaseModel

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.actions.action_execute import ActionExecutor
from haxdex.gui.file_tree.actions.action_handler import BaseAction
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeInitArgs, FileTreeNode
from beartype.typing import cast, Optional
from loguru import logger


class KnownActionData(BaseModel, extra="forbid"):
    actions: list[BaseAction]


@beartype
class KnownActionColumnSpec(FileTreeColumnSpec):
    column_name = "known_action"
    column_type = KnownActionData

    def initColumnData(
        self,
        args: FileTreeInitArgs,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> Optional[BaseModel]:
        path = args.path.resolve().absolute()
        if path in self.file_to_actions:
            return KnownActionData(actions=self.file_to_actions[path])

        else:
            return None

    def __init__(self, name: str, executor: ActionExecutor | None) -> None:
        super().__init__(name)
        self.file_to_actions: dict[Path, list[BaseAction]] = dict()
        self.executor = executor
        if self.executor:
            for act in self.executor.load_all_actions():
                assert isinstance(act, BaseAction)
                original = act.file.path.resolve().absolute()
                if original not in self.file_to_actions:
                    self.file_to_actions[original] = list()

                self.file_to_actions[original].append(act)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        node = cast(Optional[KnownActionData], self.getColumnData(index))
        if node is None:
            return None

        match role:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                result: list[str] = list()
                for act in node.actions:
                    result.append(f"{act.kind} {act.file.path}")

                return "\n".join(result)

            case CustomModelRole.FullDataRole.value:
                return node

            case _:
                return None
