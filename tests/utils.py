from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QModelIndex
from beartype import beartype

from haxdex.cli.cli import IndexService
from haxdex.cli.cli_config import AppConfig, IndexConfig, DatabaseConfig, IndexPathConfig, FileTreeViewConfig
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode
from haxdex.services.default_job_types import DEFAULT_INDEXER_TYPES, DEFAULT_RESOURCE_TYPES
from haxdex.services.file_iteration import DirConfig
from haxdex.services.indexers.exif_metadata import ExifMetadataIndexer
from haxdex.services.indexers.ffprobe_indexer import FFProbeIndexer
from haxdex.services.indexers.file_size import FileSizeIndexer


@beartype
@dataclass
class IndexServiceConfig():
    service: IndexService
    root_dir: Path
    cfg: AppConfig


def init_index_service(stable_test_dir: Path) -> IndexServiceConfig:
    root_dir = stable_test_dir.joinpath("data")
    root_dir.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        index=IndexConfig(paths=(IndexPathConfig(name="root",
                                                 root_path=root_dir,
                                                 paths=[
                                                     DirConfig(path=root_dir),
                                                 ]),),),
        indexers={
            FFProbeIndexer.asset_name: FFProbeIndexer.config_model(),
            FileSizeIndexer.asset_name: FileSizeIndexer.config_model(),
            ExifMetadataIndexer.asset_name: ExifMetadataIndexer.config_model(),
        },
        resources={},
        index_cache=stable_test_dir.joinpath("index_cache.sqlite"),
        hash_cache=stable_test_dir.joinpath("hash_cache.sqlite"),
        db=DatabaseConfig(db_name=f"service_{stable_test_dir.stem}",),
        action_file=stable_test_dir.joinpath("actions.jsonl"),
    )

    service = IndexService(cfg=cfg, only_short_curcuit_checks=False)

    return IndexServiceConfig(service=service, root_dir=root_dir, cfg=cfg)


@beartype
@dataclass
class FileTreeServiceConfig():
    service: IndexService
    root_dir: Path
    cfg: AppConfig


def init_file_tree_config(index: IndexServiceConfig) -> FileTreeServiceConfig:
    assert index.cfg.index
    cfg = index.cfg.model_copy(update=dict(
        index=None,
        file_tree_view=FileTreeViewConfig(root_dirs=index.cfg.index.paths[0].paths),
    ))

    service = IndexService(
        cfg=cfg,
        only_short_curcuit_checks=False,
    )

    return FileTreeServiceConfig(
        service=service,
        root_dir=index.root_dir,
        cfg=cfg,
    )


def sub_row_by_name(index: QModelIndex, suffix: str) -> QModelIndex:
    model = index.model()
    assert model
    for i in range(0, model.rowCount(index)):
        row_idx = model.index(i, 0, index)
        node = row_idx.internalPointer()
        assert isinstance(node, FileTreeNode)
        if str(node.path).endswith(suffix):
            return row_idx

    raise ValueError(f"no index for {suffix}")
