from beartype import beartype
from beartype.typing import Any, Optional, cast
from haxdex_core.utils import format_size
from haxdex_read_media.indexers.file_stats import (
    FileStatsIndexer,
    FileStatsIndexerResult,
)
from haxdex_view_tree.columns.file_tree_column import (
    FileTreeColumnSpec,
    FileTreeInitArgs,
    FileTreeNode,
)
from pydantic import BaseModel
from PyQt6.QtCore import QModelIndex, Qt

from haxdex.gui.common.qt_model_roles import CustomModelRole


class EntrySizeData(BaseModel, extra="forbid"):
    size_bytes: int


@beartype
class EntrySizeColumnSpec(FileTreeColumnSpec):
    column_name = "entry_size"
    column_type = EntrySizeData

    def initColumnData(
        self,
        args: FileTreeInitArgs,
        assets: dict[str, BaseModel],
        nested: list[FileTreeNode],
    ) -> Optional[BaseModel]:
        if args.is_directory:
            size_bytes = 0
            for node in nested:
                duplicate_data = cast(
                    Optional[EntrySizeData],
                    node.columns.get(self.column_name),
                )

                if duplicate_data is None:
                    continue

                size_bytes += duplicate_data.size_bytes

            return EntrySizeData(size_bytes=size_bytes)

        elif FileStatsIndexer.asset_name in assets:
            result = cast(FileStatsIndexerResult, assets[FileStatsIndexer.asset_name])
            return EntrySizeData(size_bytes=result.size_bytes)

        else:
            return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        rate = cast(Optional[EntrySizeData], self.getColumnData(index))

        if not rate:
            return None

        match role:
            case Qt.ItemDataRole.DisplayRole:
                return format_size(rate.size_bytes)

            case CustomModelRole.FullDataRole.value:
                return rate

            case (
                CustomModelRole.SortDataRole.value
                | CustomModelRole.FilterDataRole.value
            ):
                return rate.size_bytes

            case _:
                return None
