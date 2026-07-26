from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from beartype import beartype

from beartype.typing import Any, cast, Optional
from pydantic import BaseModel

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode
from haxdex.services.core.types import FileHash

import logging

log = logging.getLogger(__name__)


class IsDirectoryData(BaseModel, extra="forbid"):
    is_directory: bool


@beartype
class IsDirectoryColumnSpec(FileTreeColumnSpec):
    column_type = IsDirectoryData
    column_name = "is_directory"

    def initColumnData(
        self,
        path: Path,
        hash: Optional[FileHash],
        is_directory: bool,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> BaseModel:
        return IsDirectoryData(is_directory=is_directory)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        data = cast(IsDirectoryData, self.getColumnData(index))
        assert data is not None, "File name column spec cannot be null"
        match role:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                return str(data.is_directory)

            case CustomModelRole.FullDataRole.value:
                return data

            case _:
                return None
