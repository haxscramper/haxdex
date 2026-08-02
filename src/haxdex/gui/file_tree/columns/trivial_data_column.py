from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt
from beartype import beartype

from beartype.typing import Any, cast, Optional
from pydantic import BaseModel

from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode, FileTreeInitArgs
from haxdex.services.core.types import FileHash

from loguru import logger


class TrivialEntryData(BaseModel, extra="forbid"):
    is_directory: bool
    assets: list[str]
    root: str
    root_relative: str
    # hash: Optional[str]


@beartype
class TrivialDataColumnSpec(FileTreeColumnSpec):
    column_type = TrivialEntryData
    column_name = "trivial_data"

    def initColumnData(
        self,
        args: FileTreeInitArgs,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> BaseModel:
        return TrivialEntryData(
            is_directory=args.is_directory,
            # hash=hash.hash if hash else None,
            assets=list(sorted(assets.keys())),
            root=args.root,
            root_relative=args.relative,
        )

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        data = cast(TrivialEntryData, self.getColumnData(index))
        assert data is not None, "File name column spec cannot be null"
        match role:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                return str(data.is_directory)

            case CustomModelRole.FullDataRole.value:
                return data

            case _:
                return None
