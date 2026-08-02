from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, model_validator
from beartype.typing import ClassVar, Any, Mapping
from pydantic_core import PydanticCustomError

from haxdex.gui.file_tree.actions.action_db import OperationRow
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode


class BaseAction(BaseModel, extra="forbid"):
    file: FileTreeNode
    message: str | None = None
    kind: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def _check_and_strip_kind(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data

        data = dict(data)  # avoid mutating input
        incoming = data.pop("kind", None)

        if incoming is None:
            return data

        if incoming != cls.kind:
            raise PydanticCustomError(
                "kind_mismatch",
                f"Invalid `kind` for {cls.__name__}: expected {cls.kind!r}, got {incoming!r}",
            )

        return data


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
