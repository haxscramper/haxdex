from dataclasses import dataclass
from pathlib import Path

from beartype import beartype
from haxdex_read_media.indexers.exif_metadata import ExifMetadataIndexer
from haxdex_read_media.indexers.ffprobe_indexer import FFProbeIndexer
from haxdex_read_media.indexers.file_stats import FileStatsIndexer
from haxdex_read_media.indexers.mime_indexer import FileMimeIndexer

from haxdex_core.core_config import (
    DatabaseConfig,
    IndexConfig,
    IndexServiceConfig,
    LoggingConfig,
)
from haxdex_core.indexer_service import IndexService
from haxdex_core.job_types import BaseIndexer, BaseResource


@beartype
@dataclass
class IndexServiceTestState:
    service: IndexService
    data_dir: Path
    root_dirs: list[Path]
    root_names: list[str]
    cfg: IndexServiceConfig
    stable_test_dir: Path


@beartype
def init_core_index_service(
    stable_test_dir: Path,
    indexer_types: list[type[BaseIndexer]],
    resource_types: list[type[BaseResource]],
    root_names: list[str] | None = None,
    reset: bool = True,
) -> IndexServiceTestState:
    data_dir = stable_test_dir.joinpath("data")
    data_dir.mkdir(parents=True, exist_ok=True)

    if root_names is None:
        root_names = []
        root_dirs = [data_dir]
    else:
        root_dirs = [data_dir.joinpath(root_name) for root_name in root_names]
        for root_dir in root_dirs:
            root_dir.mkdir(parents=True, exist_ok=True)

    act_conf = ActionExecutionConfig(
        trash_root=stable_test_dir.joinpath("trash"),
        sqlite_path=stable_test_dir.joinpath("actions.sqlite"),
        output_directory=stable_test_dir.joinpath("action_output"),
        dry_run=False,
    )

    act_conf.trash_root.mkdir(parents=True, exist_ok=True)
    act_conf.output_directory.mkdir(parents=True, exist_ok=True)

    if root_names:
        index_paths = tuple(
            IndexPathConfig(
                name=root_name,
                root_path=root_dir,
                paths=[DirConfig(path=root_dir)],
            )
            for root_name, root_dir in zip(root_names, root_dirs)
        )
    else:
        index_paths = (
            IndexPathConfig(
                name="data",
                root_path=data_dir,
                paths=[DirConfig(path=data_dir)],
            ),
        )

    cfg = IndexServiceConfig(
        index=IndexConfig(
            paths=index_paths,
            reset=True,
        ),
        indexers={
            FFProbeIndexer.asset_name: FFProbeIndexer.config_model(),
            ExifMetadataIndexer.asset_name: ExifMetadataIndexer.config_model(),
            FileStatsIndexer.asset_name: FileStatsIndexer.config_model(),
            FileMimeIndexer.asset_name: FileMimeIndexer.config_model(),
        },
        resources={},
        hash_cache=stable_test_dir.joinpath("hash_cache.sqlite"),
        db=DatabaseConfig(db_name=f"service_{stable_test_dir.stem}"),  # type: ignore
        # action_file=stable_test_dir.joinpath("actions.jsonl"),
        # act=ActionConfig(execution=act_conf),
        logging=LoggingConfig(),
    )

    if reset:
        IndexService.reset_for_config(cfg)

    service = IndexService(
        cfg=cfg,
        only_short_curcuit_checks=False,
        indexer_types=indexer_types,
        resource_types=resource_types,
    )

    return IndexServiceConfig(
        service=service,
        data_dir=data_dir,
        root_dirs=root_dirs,
        root_names=root_names,
        cfg=cfg,
        stable_test_dir=stable_test_dir,
    )
