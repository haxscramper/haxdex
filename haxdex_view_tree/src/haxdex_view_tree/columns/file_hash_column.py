from beartype import beartype
from beartype.typing import Any, Optional, cast
from haxdex_view_tree.columns.file_tree_column import (
    FileTreeColumnSpec,
    FileTreeInitArgs,
    FileTreeNode,
)
from pydantic import BaseModel
from PyQt6.QtCore import QModelIndex, Qt

from haxdex.gui.common.qt_model_roles import CustomModelRole


class FileHashData(BaseModel, extra="forbid"):
    hash: str


@beartype
class FileHashColumnSpec(FileTreeColumnSpec):
    column_name = "file_hash"
    column_type = FileHashData

    def initColumnData(
        self,
        args: FileTreeInitArgs,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> Optional[BaseModel]:
        if hash:
            return FileHashData(hash=args.hash.hash)

        else:
            return None

    def __init__(self) -> None:
        super().__init__("Hash")

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        node = cast(FileHashData, self.getColumnData(index))

        match role, node:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole, FileHashData():
                return node.hash

            case CustomModelRole.FullDataRole.value:
                return node

            case (
                CustomModelRole.SortDataRole.value
                | CustomModelRole.FilterDataRole.value
            ):
                return node.hash

            case _:
                return None
