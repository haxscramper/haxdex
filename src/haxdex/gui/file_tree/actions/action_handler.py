from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel
from beartype.typing import ClassVar

from haxdex.gui.file_tree.actions.action_db import OperationRow
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode


class BaseAction(BaseModel, extra="forbid"):
    file: FileTreeNode
    kind: ClassVar[str]


class ActionHandler(ABC):
    action_type: ClassVar[type[BaseAction]]

    @abstractmethod
    def do_action(self, row: OperationRow, action: BaseAction) -> None:
        raise NotImplementedError

    @abstractmethod
    def undo_action(self, row: OperationRow, action: BaseAction) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_hash(self, action: BaseAction) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify_consistency_single(self, action: BaseAction) -> None:
        raise NotImplementedError


class ActionResult(ABC):

    @abstractmethod
    def getResultPaths(self) -> list[Path]:
        raise NotImplementedError
