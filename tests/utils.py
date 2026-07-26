from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QModelIndex
from beartype import beartype
from beartype.typing import cast

from haxdex.cli.cli import IndexService
from haxdex.cli.cli_config import AppConfig, IndexConfig, DatabaseConfig, IndexPathConfig, FileTreeViewConfig
from haxdex.gui.file_tree.columns.file_mime_column import FileMimeColumnSpec
from haxdex.gui.file_tree.columns.file_name_column import FileNameColumnSpec
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode
from haxdex.gui.file_tree.columns.trivial_data_column import TrivialDataColumnSpec
from haxdex.gui.file_tree.columns.size_column import EntrySizeColumnSpec
from haxdex.gui.file_tree.columns.size_share_column import SizeShareColumnSpec
from haxdex.gui.file_tree.columns.video_bitrate_columns import VideoBitrateData, VideoBitrateColumnSpec
from haxdex.gui.file_tree.columns.video_framerate_column import VideoFramerateColumnSpec
from haxdex.gui.file_tree.columns.video_resolution_column import VideoResolutionColumnSpec
from haxdex.services.default_job_types import DEFAULT_INDEXER_TYPES, DEFAULT_RESOURCE_TYPES
from haxdex.services.file_iteration import DirConfig
from haxdex.services.indexers.exif_metadata import ExifMetadataIndexer
from haxdex.services.indexers.ffprobe_indexer import FFProbeIndexer
from haxdex.services.indexers.file_size import FileSizeIndexer
from haxdex.services.indexers.file_stats import FileStatsIndexer
from haxdex.services.indexers.mime_indexer import FileMimeIndexer


@beartype
@dataclass
class IndexServiceConfig():
    service: IndexService
    root_dir: Path
    cfg: AppConfig
    stable_test_dir: Path


def init_index_service(stable_test_dir: Path) -> IndexServiceConfig:
    root_dir = stable_test_dir.joinpath("data")
    root_dir.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        index=IndexConfig(
            paths=(IndexPathConfig(name="root",
                                   root_path=root_dir,
                                   paths=[
                                       DirConfig(path=root_dir),
                                   ]),),
            reset=True,
        ),
        indexers={
            FFProbeIndexer.asset_name: FFProbeIndexer.config_model(),
            ExifMetadataIndexer.asset_name: ExifMetadataIndexer.config_model(),
            FileStatsIndexer.asset_name: FileStatsIndexer.config_model(),
            FileMimeIndexer.asset_name: FileMimeIndexer.config_model(),
        },
        resources={},
        index_cache=stable_test_dir.joinpath("index_cache.sqlite"),
        hash_cache=stable_test_dir.joinpath("hash_cache.sqlite"),
        db=DatabaseConfig(db_name=f"service_{stable_test_dir.stem}",),
        action_file=stable_test_dir.joinpath("actions.jsonl"),
    )

    IndexService.reset_for_config(cfg)
    service = IndexService(cfg=cfg, only_short_curcuit_checks=False)

    return IndexServiceConfig(
        service=service,
        root_dir=root_dir,
        cfg=cfg,
        stable_test_dir=stable_test_dir,
    )


@beartype
def init_file_tree_columns(index: IndexServiceConfig) -> list[FileTreeColumnSpec]:
    return cast(list[FileTreeColumnSpec], [
        FileNameColumnSpec(),
        TrivialDataColumnSpec("trivial"),
        FileMimeColumnSpec("mime"),
        EntrySizeColumnSpec("size"),
        SizeShareColumnSpec("share", [index.root_dir]),
        VideoFramerateColumnSpec("framerate"),
        VideoBitrateColumnSpec("bitrate"),
        VideoResolutionColumnSpec("video_resolution"),
    ])


@beartype
@dataclass
class FileTreeServiceConfig():
    service: IndexService
    root_dir: Path
    cfg: AppConfig
    stable_test_dir: Path


def init_file_tree_config(index: IndexServiceConfig) -> FileTreeServiceConfig:
    assert index.cfg.index
    cfg = index.cfg.model_copy(update=dict(
        index=None,
        file_tree_view=FileTreeViewConfig(
            root_dirs=index.cfg.index.paths[0].paths,
            reference_tree_cache_path=index.stable_test_dir.joinpath(
                "reference_tree_cache.sqlite"),
            visual_tree_cache_path=index.stable_test_dir.joinpath(
                "input_tree_cache.sqlite"),
            user_edit_path=index.stable_test_dir.joinpath("user_actions.sqlite"),
        ),
    ))

    service = IndexService(
        cfg=cfg,
        only_short_curcuit_checks=False,
    )

    return FileTreeServiceConfig(
        service=service,
        root_dir=index.root_dir,
        cfg=cfg,
        stable_test_dir=index.stable_test_dir,
    )


def sub_row_by_name(index: QModelIndex, suffix: str, name_column: int = 0) -> QModelIndex:
    model = index.model()
    assert model
    for i in range(0, model.rowCount(index)):
        row_idx = model.index(i, name_column, index)
        node = row_idx.internalPointer()
        assert isinstance(node, FileTreeNode)
        if str(node.path).endswith(suffix):
            return row_idx

    raise ValueError(f"no index for {suffix}")
