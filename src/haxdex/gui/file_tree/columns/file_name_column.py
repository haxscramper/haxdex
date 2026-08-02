from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from beartype import beartype

from beartype.typing import Any, cast, Optional
from pydantic import BaseModel

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode, FileTreeInitArgs
from haxdex.services.core.types import FileHash

from loguru import logger


class FileNameData(BaseModel, extra="forbid"):
    name: str


@beartype
class FileNameColumnSpec(FileTreeColumnSpec):
    column_type = FileNameData
    column_name = "file_name"

    def initColumnData(
        self,
        args: FileTreeInitArgs,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> BaseModel:
        return FileNameData(name=args.path.name)

    def __init__(self) -> None:
        super().__init__("name")

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        data = cast(FileNameData, self.getColumnData(index))
        assert data is not None, "File name column spec cannot be null"
        match role:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                return data.name

            case CustomModelRole.FullDataRole.value:
                return data

            case CustomModelRole.SortDataRole.value | CustomModelRole.FilterDataRole.value:
                return data.name

            case _:
                return None
