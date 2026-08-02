from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from beartype import beartype

from haxdex.gui.agnostic.column_model import ColumnSpec
from beartype.typing import Any, cast, Optional, ClassVar
from pydantic import BaseModel, Field
from abc import ABC, abstractmethod

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.services.core.types import AnyModel, FileHash


class FilePathResult(BaseModel):
    path: Path
    hash: str


class FileTreeNode(BaseModel):
    path: Path
    is_directory: bool
    root: str
    root_relative: str
    hash: FileHash | None = None
    columns: dict[str, Optional[BaseModel]] = Field(default_factory=dict)
    nested: list["FileTreeNode"] = Field(default_factory=list)


@beartype
@dataclass
class FileTreeInitArgs():
    is_directory: bool
    path: Path
    root: str
    relative: str
    hash: Optional[FileHash] = None


@beartype
class FileTreeColumnSpec(ColumnSpec, ABC):

    def __init__(self, title: str) -> None:
        self.title = title

    column_type: ClassVar[type[AnyModel]]
    column_name: ClassVar[str]

    @abstractmethod
    def initColumnData(
        self,
        args: FileTreeInitArgs,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> Optional[BaseModel]:
        "Extract subset of data from assets to the column"
        raise NotImplementedError()

    def getColumnData(self, index: QModelIndex) -> Optional[BaseModel]:
        result = cast(FileTreeNode, index.internalPointer()).columns.get(self.column_name)
        assert result is None or isinstance(result, self.column_type), f"{type(result)}"
        return result

    def setColumnData(self, index: QModelIndex, value: BaseModel):
        assert isinstance(value, self.column_type)
        cast(FileTreeNode, index.internalPointer()).columns[self.column_name] = value

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        match orientation, role:
            case Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole:
                return self.title

            case CustomModelRole.SortDataRole.value | CustomModelRole.FilterDataRole.value:
                return self.title

            case _:
                return None
