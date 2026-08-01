import dataclasses
import functools
import logging
import shutil
from contextlib import contextmanager, redirect_stderr
from dataclasses import dataclass
from pathlib import Path

import glom
from PyQt6.QtCore import QModelIndex, QAbstractItemModel
from beartype import beartype
from beartype.typing import cast, Iterator, Any

import pandas as pd

from haxdex.cli.cli import IndexService
from haxdex.cli.cli_config import ActionConfig, AppConfig, IndexConfig, DatabaseConfig, IndexPathConfig, FileTreeViewConfig
from haxdex.gui.file_tree.actions.action_execute import ActionExecutionConfig, ActionExecutor
from haxdex.gui.file_tree.columns.file_duplicate_column import FileDuplicateColumnSpec
from haxdex.gui.file_tree.columns.file_mime_column import FileMimeColumnSpec
from haxdex.gui.file_tree.columns.file_name_column import FileNameColumnSpec
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode
from haxdex.gui.file_tree.columns.known_actions_column import KnownActionColumnSpec
from haxdex.gui.file_tree.columns.trivial_data_column import TrivialDataColumnSpec
from haxdex.gui.file_tree.columns.size_column import EntrySizeColumnSpec
from haxdex.gui.file_tree.columns.size_share_column import SizeShareColumnSpec
from haxdex.gui.file_tree.columns.video_bitrate_columns import VideoBitrateColumnSpec
from haxdex.gui.file_tree.columns.video_framerate_column import VideoFramerateColumnSpec
from haxdex.gui.file_tree.columns.video_resolution_column import VideoResolutionColumnSpec
from haxdex.services.file_iteration import DirConfig
from haxdex.services.indexers.exif_metadata import ExifMetadataIndexer
from haxdex.services.indexers.ffprobe_indexer import FFProbeIndexer
from haxdex.services.indexers.file_stats import FileStatsIndexer
from haxdex.services.indexers.mime_indexer import FileMimeIndexer


@beartype
@dataclass
class IndexServiceConfig():
    service: IndexService
    data_dir: Path
    root_dirs: list[Path]
    root_names: list[str]
    cfg: AppConfig
    stable_test_dir: Path


@beartype
def init_index_service(
    stable_test_dir: Path,
    root_names: list[str] | None = None,
    reset: bool = True,
) -> IndexServiceConfig:
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
            ) for root_name, root_dir in zip(root_names, root_dirs))
    else:
        index_paths = (IndexPathConfig(
            name="data",
            root_path=data_dir,
            paths=[DirConfig(path=data_dir)],
        ),)

    cfg = AppConfig(
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
        index_cache=stable_test_dir.joinpath("index_cache.sqlite"),
        hash_cache=stable_test_dir.joinpath("hash_cache.sqlite"),
        db=DatabaseConfig(db_name=f"service_{stable_test_dir.stem}"),
        action_file=stable_test_dir.joinpath("actions.jsonl"),
        act=ActionConfig(execution=act_conf),
    )

    if reset:
        IndexService.reset_for_config(cfg)

    service = IndexService(cfg=cfg, only_short_curcuit_checks=False)

    return IndexServiceConfig(
        service=service,
        data_dir=data_dir,
        root_dirs=root_dirs,
        root_names=root_names,
        cfg=cfg,
        stable_test_dir=stable_test_dir,
    )


@beartype
def init_file_tree_columns(
        index: IndexServiceConfig,
        reference_tree: FileTreeNode | None = None) -> list[FileTreeColumnSpec]:
    specs = cast(list[FileTreeColumnSpec], [
        FileNameColumnSpec(),
        TrivialDataColumnSpec("trivial"),
        FileMimeColumnSpec("mime"),
        EntrySizeColumnSpec("size"),
        SizeShareColumnSpec("share", [index.data_dir]),
        VideoFramerateColumnSpec("framerate"),
        VideoBitrateColumnSpec("bitrate"),
        VideoResolutionColumnSpec("video_resolution"),
    ])

    if reference_tree is not None:
        specs.append(
            FileDuplicateColumnSpec("file_duplicates", reference_tree=reference_tree))

    if index.cfg.act and index.cfg.act.execution.sqlite_path.exists():
        executor = ActionExecutor(index.cfg.act.execution)
        specs.append(KnownActionColumnSpec("actions", executor))

    return specs


@beartype
@dataclass
class FileTreeServiceConfig():
    service: IndexService
    data_dir: Path
    root_dirs: list[Path]
    root_names: list[str]
    cfg: AppConfig
    stable_test_dir: Path


def init_file_tree_config(index: IndexServiceConfig) -> FileTreeServiceConfig:
    assert index.cfg.index
    all_root_dirs = [
        dir_cfg for path_cfg in index.cfg.index.paths for dir_cfg in path_cfg.paths
    ]
    cfg = index.cfg.model_copy(update=dict(
        index=None,
        file_tree_view=FileTreeViewConfig(
            root_dirs=all_root_dirs,
            reference_tree_cache_path=index.stable_test_dir.joinpath(
                "reference_tree_cache.sqlite"),
            visual_tree_cache_path=index.stable_test_dir.joinpath(
                "input_tree_cache.sqlite"),
            user_edit_path=index.stable_test_dir.joinpath("user_actions.sqlite"),
            reference_dir=DirConfig(path=index.data_dir),
        ),
    ))

    service = IndexService(
        cfg=cfg,
        only_short_curcuit_checks=False,
    )

    return FileTreeServiceConfig(
        service=service,
        data_dir=index.data_dir,
        root_dirs=index.root_dirs,
        root_names=index.root_names,
        cfg=cfg,
        stable_test_dir=index.stable_test_dir,
    )


def sub_row_by_name(index: QModelIndex,
                    model: QAbstractItemModel,
                    suffix: str,
                    name_column: int = 0) -> QModelIndex:
    for i in range(0, model.rowCount(index)):
        row_idx = model.index(i, name_column, index)
        node = row_idx.internalPointer()
        assert isinstance(node, FileTreeNode)
        if str(node.path).endswith(suffix):
            return row_idx

    raise ValueError(f"no index for {suffix}")


@contextmanager
def capture_all_logs_to_test_file(
    stable_test_dir: Path,
    test_name: str,
    level: int = logging.DEBUG,
) -> Iterator[Path]:
    run_log_path = stable_test_dir / f"{test_name}"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    old_root_handlers = list(root.handlers)
    old_root_level = root.level

    touched: list[tuple[logging.Logger, bool]] = []
    for obj in logging.root.manager.loggerDict.values():
        if isinstance(obj, logging.Logger):
            touched.append((obj, obj.propagate))
            obj.propagate = True

    run_log_path.write_text("")
    with run_log_path.open("w", encoding="utf-8") as log_file, redirect_stderr(log_file):
        root.handlers.clear()
        root.setLevel(level)

        handler = logging.StreamHandler(log_file)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                "%(levelname)s %(name)s %(filename)s:%(lineno)d: %(message)s"))
        root.addHandler(handler)

        try:
            yield run_log_path
        finally:
            root.removeHandler(handler)
            handler.close()
            root.handlers[:] = old_root_handlers
            root.setLevel(old_root_level)
            for logger, old_propagate in touched:
                logger.propagate = old_propagate


def capture_logs(test_name: str | None = None, level: int = logging.DEBUG):

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with capture_all_logs_to_test_file(
                    kwargs["stable_test_dir"],
                    test_name or func.__name__,
                    level,
            ):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def clean_test_dir(func=None):

    def decorator(f):

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            test_dir: Path = kwargs["stable_test_dir"]
            if test_dir.exists():
                shutil.rmtree(test_dir)

            test_dir.mkdir(parents=True, exist_ok=True)
            return f(*args, **kwargs)

        return wrapper

    return decorator if func is None else decorator(func)


def _cell_to_dict(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if dataclasses.is_dataclass(v):
        return dataclasses.asdict(v)
    if hasattr(v, "model_dump"):  # pydantic v2
        return v.model_dump()
    if hasattr(v, "__dict__"):
        return {k: val for k, val in vars(v).items() if not k.startswith("_")}
    return {}


@beartype
def split_columns_by_rules(
    df: pd.DataFrame,
    rules: dict[str, list[str | tuple[Any, str]]],
) -> pd.DataFrame:
    out = df.copy()

    for col in df.columns:
        if col not in rules:
            continue

        mapped = out[col].map(_cell_to_dict)
        fields = rules[col]

        for field in fields:
            if isinstance(field, str):
                field_key, res_name = field, field

            else:
                field_key, res_name = field

            out[res_name] = mapped.map(lambda d: glom.glom(d, field_key, default=None))

    columns_to_drop: list[str] = []

    for col in rules.keys():
        if col in out.columns:
            columns_to_drop.append(col)

    out = out.drop(columns=columns_to_drop)

    return out
