from contextlib import ExitStack
import dataclasses
import functools
import itertools
import json
from loguru import logger
import shutil
from collections import defaultdict
from pathlib import Path
from pprint import pformat
from typing import List, cast

import hypothesis.strategies as st
import pandas as pd
import plumbum
import pytest
from beartype import beartype
from beartype.typing import Any, List
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis.control import current_build_context
from PyQt6.QtCore import QAbstractItemModel, QModelIndex
from sqlalchemy import select
from sqlalchemy.orm import Session

from haxdex.cli.cli import IndexService, main_impl
from haxdex.cli.cli_config import AppConfig, IndexConfig, IndexPathConfig, DatabaseConfig, LoggingConfig
from haxdex.gui.agnostic import model_dump
from haxdex.gui.agnostic.model_dump import render_text, simple_dump, simple_dump_rows_json
from haxdex.gui.agnostic.tree_to_table_model import TreeToTableProxyModel
from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.common.qt_utils import qt_model_to_dataframe
from haxdex.gui.file_tree.actions.action_db import OperationRow
from haxdex.gui.file_tree.actions.action_execute import _DONE_STR, ActionExecutor
from unittest.mock import patch
from haxdex.gui.file_tree.actions.action_list_model import (
    ActionListModel,
    ActionProvider,
    TrashAction,
)
from haxdex.gui.file_tree.columns.file_duplicate_column import (
    FileDuplicateColumnSpec,
    FileDuplicateData,
)
from haxdex.gui.file_tree.columns.file_mime_column import FileMimeColumnSpec, FileMimeData
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeColumnSpec, FileTreeNode
from haxdex.gui.file_tree.columns.known_actions_column import KnownActionColumnSpec
from haxdex.gui.file_tree.columns.trivial_data_column import (
    TrivialDataColumnSpec,
    TrivialEntryData,
)
from haxdex.gui.file_tree.model.tree_model_fetch import AQL_FILE_PATHS, fetch_file_paths, _fetch_file_paths_impl
from haxdex.gui.file_tree.qt_tree_model import FileTreeModel
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.gui.file_tree.query_filter import QueryFilterEvaluator, QueryProgram
from haxdex.services.core.hash_cache import HashCache
from haxdex.services.file_iteration import prepare_root_filters, DirConfig
from haxdex.services.indexers.exif_metadata import ExifMetadataIndexer
from haxdex.services.indexers.ffprobe_indexer import FFProbeIndexer
from haxdex.services.indexers.file_stats import FileStatsIndexer
from haxdex.services.indexers.full_document.full_document import DocumentBlockIndexer
from haxdex.services.indexers.mime_indexer import FileMimeIndexer
from haxdex.services.pydantic_utils import format_json_with_fjson, model_from_json_data, to_json_safe
from tests.generation import (
    META_SUFFIX,
    GeneratedDirectory,
    GeneratedIndexerEntry,
    MaterializedDirectory,
    _sorted_rel,
    assert_generated_directory_entries_exact,
    create_default_persistent_corpus,
    directory_structure,
    write_generated_directory,
    GeneratedIndexerFile,
)
from tests.utils import (
    FileTreeServiceConfig,
    capture_logs,
    clean_test_dir,
    init_file_tree_columns,
    init_file_tree_config,
    init_index_service,
    split_columns_by_rules,
    sub_row_by_name,
    IndexServiceConfig,
)

corpus_root = Path("/tmp/haxdex_tests/pbt_corpus")
corpus_manifest = create_default_persistent_corpus(corpus_root)

pd.set_option("display.max_rows", None)
pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
pd.set_option("display.max_colwidth", None)
pd.set_option("display.colheader_justify", "left")


@beartype
def left_align_formatters(df):
    widths = {
        column: max(len(str(column)), df[column].map(str).str.len().max())
        for column in df.columns
    }

    return [(lambda width: lambda value: f"{str(value):<{width}}")(widths[column])
            for column in df.columns]


@beartype
def qt_tree_to_df(model: QAbstractItemModel) -> pd.DataFrame:
    table = TreeToTableProxyModel()
    table.setSourceModel(model)
    return qt_model_to_dataframe(
        model=table,
        role=CustomModelRole.FullDataRole.value,
        role_names={CustomModelRole.FullDataRole.value: "data"},
    )


@beartype
def fmt_df(df: pd.DataFrame) -> str:
    return df.to_string(justify="left", formatters=left_align_formatters(df))


@beartype
def _fs_content_files(root: Path) -> list[Path]:
    return _sorted_rel(
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not path.name.endswith(META_SUFFIX))


@beartype
def _all_query_paths_from_files(files: list[Path]) -> list[Path]:
    result: set[Path] = {Path(".")}
    for rel in files:
        result.update(rel.parents)
    return _sorted_rel(result)


@beartype
def _fs_files_direct(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(
        path.relative_to(root)
        for path in base.glob("*")
        if path.is_file() and not path.name.endswith(META_SUFFIX))


@beartype
def _fs_files_recursive(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(
        path.relative_to(root)
        for path in base.rglob("*")
        if path.is_file() and not path.name.endswith(META_SUFFIX))


@beartype
def _fs_directories_direct(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(path.relative_to(root) for path in base.glob("*") if path.is_dir())


@beartype
def _fs_directories_recursive(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(
        path.relative_to(root) for path in base.rglob("*") if path.is_dir())


@beartype
def _fs_entries_direct(root: Path, query: Path) -> list[Path]:
    return _sorted_rel([
        *_fs_files_direct(root, query),
        *_fs_directories_direct(root, query),
    ])


@beartype
def _fs_entries_recursive(root: Path, query: Path) -> list[Path]:
    return _sorted_rel([
        *_fs_files_recursive(root, query),
        *_fs_directories_recursive(root, query),
    ])


@beartype
def _relset(items: list[GeneratedIndexerEntry]) -> set[Path]:
    return {item.relative_path for item in items}


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(directory=directory_structure(
    indexer_types=[],
    min_files=2,
    max_files=16,
    min_nesting=1,
    max_nesting=4,
    corpus_manifest=corpus_manifest,
    corpus_root=corpus_root,
    mime_types=(
        "image/png",
        "application/pdf",
        "video/mp4",
        "audio/mpeg",
        "text/plain",
        "text/org",
        "text/markdown",
    ),
))
def test_generated_directory_collect_methods_match_content(
    stable_test_dir: Path,
    directory: GeneratedDirectory,
) -> None:
    import shutil

    if stable_test_dir.exists():
        shutil.rmtree(stable_test_dir)

    stable_test_dir.mkdir(parents=True, exist_ok=True)

    gen_dir = stable_test_dir / "data"

    materialized = write_generated_directory(gen_dir, directory)
    assert_generated_directory_entries_exact(materialized.root, directory)

    physical_dir_listing = plumbum.local["exa"].run(["--tree", str(gen_dir)])
    dbg = f"full directory listing:\n{physical_dir_listing}"

    def pformat_dir(value: Any) -> str:
        return f"""
{pformat(value)}
{dbg}
        """

    fs_files = _fs_content_files(materialized.root)
    assert set(fs_files) == {item.relative_path for item in directory.files}

    # collect_entries_direct() default argument
    direct_entries_default = [p.relative_path for p in directory.collect_entries_direct()]
    fs_entries_default = [
        p.relative_to(materialized.root) for p in materialized.root.glob("*")
    ]
    assert len(direct_entries_default) == len(fs_entries_default), pformat_dir(
        dict(direct_entries=direct_entries_default, file_entries=fs_entries_default))

    assert set(direct_entries_default) == set(
        _fs_entries_direct(materialized.root, Path("."))), dbg

    # collect_entries_direct(Path(".")) explicitly
    assert _relset(directory.collect_entries_direct(Path("."))) == set(
        _fs_entries_direct(materialized.root, Path(".")))

    for query in _all_query_paths_from_files(fs_files):
        expected_files_direct = set(_fs_files_direct(materialized.root, query))
        actual_files_direct = _relset(directory.collect_files_direct(query))
        assert len(actual_files_direct) == len(expected_files_direct), pformat_dir(
            dict(
                query=query,
                actual=sorted(actual_files_direct),
                expected=sorted(expected_files_direct),
            ))

        assert actual_files_direct == expected_files_direct

        expected_files_recursive = set(_fs_files_recursive(materialized.root, query))
        actual_files_recursive = _relset(directory.collect_files_recursive(query))
        assert len(actual_files_recursive) == len(expected_files_recursive), (pformat_dir(
            dict(
                query=query,
                actual=sorted(actual_files_recursive),
                expected=sorted(expected_files_recursive),
            )))
        assert actual_files_recursive == expected_files_recursive

        expected_dirs_direct = set(_fs_directories_direct(materialized.root, query))
        actual_dirs_direct = _relset(directory.collect_directories_direct(query))
        assert len(actual_dirs_direct) == len(expected_dirs_direct), pformat_dir(
            dict(
                query=query,
                actual=sorted(actual_dirs_direct),
                expected=sorted(expected_dirs_direct),
            ))
        assert actual_dirs_direct == expected_dirs_direct

        expected_dirs_recursive = set(_fs_directories_recursive(materialized.root, query))
        actual_dirs_recursive = _relset(directory.collect_directories_recursive(query))
        assert len(actual_dirs_recursive) == len(expected_dirs_recursive), pformat_dir(
            dict(
                query=query,
                actual=sorted(actual_dirs_recursive),
                expected=sorted(expected_dirs_recursive),
            ))
        assert actual_dirs_recursive == expected_dirs_recursive

        expected_entries_direct = set(_fs_entries_direct(materialized.root, query))
        actual_entries_direct = _relset(directory.collect_entries_direct(query))
        assert len(actual_entries_direct) == len(expected_entries_direct), pformat_dir(
            dict(
                query=query,
                actual=sorted(actual_entries_direct),
                expected=sorted(expected_entries_direct),
            ))
        assert actual_entries_direct == expected_entries_direct

        expected_entries_recursive = set(_fs_entries_recursive(materialized.root, query))
        actual_entries_recursive = _relset(directory.collect_entries_recursive(query))
        assert len(actual_entries_recursive) == len(expected_entries_recursive), (
            pformat_dir(
                dict(
                    query=query,
                    actual=sorted(actual_entries_recursive),
                    expected=sorted(expected_entries_recursive),
                )))
        assert actual_entries_recursive == expected_entries_recursive

    for rel in fs_files:
        assert directory.get_file_by_relative_name(rel).relative_path == rel

    with pytest.raises(KeyError):
        directory.get_file_by_relative_name(Path("__missing__") / "nope.txt")


_DF_SPLIT_RULES = {
    "trivial": ["assets", "is_directory", "root", "root_relative"],
    "share": ["size_self", "size_parent"],
    "mime": ["mime_type"],
    "name": [("name", "entry_name")],
    "framerate": [("probe.fps", "video_framerate")],
    "bitrate": [("probe.bitrate_bps", "video_bitrate")],
    "video_resolution": [
        ("probe.width", "video_width"),
        ("probe.height", "video_height"),
    ],
    "file_duplicates": [
        ("hash", "file_hash"),
        ("matches", "duplicate_paths"),
        ("duplicate_count", "rec_duplicate_count"),
        ("total_count", "rec_total_count"),
    ],
}


@beartype
def run_main_model_splicing(
    df: pd.DataFrame,
    stable_test_dir: Path,
    expected_entries: list[Path],
    core: FileTreeQueryCore,
    gen_dir: Path,
    expected_file_count: int,
    directories: list[GeneratedDirectory],
) -> pd.DataFrame:
    assert "name" in df.columns, str(df.columns)

    stable_test_dir.joinpath("df_pre_split.json").write_text(
        json.dumps(to_json_safe(df), indent=2))
    stable_test_dir.joinpath("base_flat_model.log").write_text(fmt_df(df))
    df = split_columns_by_rules(df, _DF_SPLIT_RULES).sort_values("root_relative")
    stable_test_dir.joinpath("df_post_split.json").write_text(
        json.dumps(to_json_safe(df), indent=2))

    stable_test_dir.joinpath("base_spliced_model.log").write_text(fmt_df(df))
    expected_root_relative = {
        str(Path(*p.parts[1:])) if len(p.parts) > 1 else "" for p in expected_entries
    }

    assert expected_root_relative == set(df["root_relative"]), pformat(
        dict(
            expected_root_relative=expected_root_relative,
            model_root_relative=set(df["root_relative"]),
            original_model=simple_dump(core.model, max_col=1),
            real_directory=plumbum.local["exa"].run(["--tree", str(gen_dir)]),
        ))

    assert df["size_self"].notna().all(), "`size_self` contains None values"
    assert df["size_self"].ne(0).all(), "`size_self` contains zero values"
    assert (df["size_self"]
            <= df["size_parent"]).all(), ("entry size cannot be larger than the parent")

    video_mask = df["mime_type"].str.startswith("video/", na=False)

    assert df.loc[video_mask, "video_framerate"].notna().all()
    assert df.loc[video_mask, "video_bitrate"].notna().all()
    assert df.loc[video_mask, "video_width"].notna().all()
    assert df.loc[video_mask, "video_height"].notna().all()

    assert len(df[~df["is_directory"]]) == expected_file_count

    assert len(df.loc[df["root_relative"].eq("")]) == len(directories)
    assert len(df[~df["is_directory"]]) == df.loc[df["root_relative"].eq(""),
                                                  "rec_total_count"].sum()

    return df


@beartype
def run_video_file_filtering(df: pd.DataFrame, core: FileTreeQueryCore,
                             stable_test_dir: Path):
    video_eval = QueryFilterEvaluator()

    nodes_visited = 0
    mime_types: defaultdict[str, int] = defaultdict(lambda: 0)

    def get_only_video(nodes: list[FileTreeNode]) -> list[FileTreeNode]:
        result = list()
        nonlocal nodes_visited
        nodes_visited += len(nodes)
        for node in nodes:
            assert TrivialDataColumnSpec.column_name in node.columns
            trivial = node.columns[TrivialDataColumnSpec.column_name]
            assert isinstance(trivial, TrivialEntryData)
            if trivial.is_directory:
                result.append(node)

            else:
                assert FileMimeColumnSpec.column_name in node.columns, str(
                    node.columns.keys())
                mime = node.columns[FileMimeColumnSpec.column_name]
                assert isinstance(mime, FileMimeData) or mime is None
                if mime is not None:
                    mime_types[mime.mime_type] += 1
                    if mime.mime_type.startswith("video/"):
                        result.append(node)

        return result

    video_only_model = video_eval.filter_model(
        core.model,
        query_text=QueryProgram(filter_fn=get_only_video),
    )

    # FIXME: Root `data` nodes are not visited during iteration
    assert nodes_visited == len(df)
    assert len(mime_types) == df["mime_type"].nunique()

    video_df = qt_tree_to_df(video_only_model)

    @beartype
    def map_trivial(trivial: TrivialEntryData) -> bool:
        return not trivial.is_directory

    video_df = video_df[video_df["trivial"].map(map_trivial)]
    assert len(df[df["mime_type"].str.startswith("video/")]) == len(video_df)
    stable_test_dir.joinpath("video_df.json").write_text(
        json.dumps(to_json_safe(video_df), indent=2))


@beartype
def run_remove_duplicate_action(df: pd.DataFrame, core: FileTreeQueryCore, cfg: AppConfig,
                                data_dir: Path):
    evaluator = QueryFilterEvaluator()

    act = ActionProvider()

    kept: set[str] = set()
    duplicate_files: int = 0
    deleted_files: List[tuple[str, str]] = list()

    def actions(act: ActionProvider, nodes: list[FileTreeNode]):
        nonlocal duplicate_files
        nonlocal deleted_files
        for node in nodes:
            duplicates = node.columns[FileDuplicateColumnSpec.column_name]
            trivial = node.columns[TrivialDataColumnSpec.column_name]
            assert isinstance(trivial, TrivialEntryData)
            assert isinstance(duplicates, FileDuplicateData)
            if not (0 < duplicates.duplicate_count and duplicates.hash is not None):
                continue

            duplicate_files += 1

            h = duplicates.hash
            if h in kept:
                # trash all but first entry of this hash
                act.trash(node)
                deleted_files.append((trivial.root, trivial.root_relative))

            else:
                kept.add(h)

    action_model = evaluator.filter_model(
        core.model,
        query_text=QueryProgram(actions_fn=actions, action_provider=act),
    )

    assert isinstance(action_model, ActionListModel)

    assert len(action_model.actions()) == len(deleted_files)

    with_duplicates = df[(1 <= df["rec_duplicate_count"]) &
                         (df["is_directory"] == False)].copy()
    with_duplicates["duplicate_paths"] = with_duplicates["duplicate_paths"].map(
        lambda paths: list(map(lambda it: it.name, paths)))
    with_duplicates = with_duplicates[[
        "root",
        "is_directory",
        "rec_duplicate_count",
        "root_relative",
        "duplicate_paths",
    ]]
    with_duplicates["no_first"] = with_duplicates["rec_duplicate_count"] / (
        with_duplicates["rec_duplicate_count"] + 1)

    # logger.info(f"with duplicates:\n{fmt_df(with_duplicates)}")
    assert len(with_duplicates) == duplicate_files
    assert round(with_duplicates["no_first"].sum()) == len(action_model.actions())

    assert cfg.act
    executor = ActionExecutor(config=cfg.act.execution)
    executor.init_db()
    # in the main GUI/CLI this step is done by
    # - store actions to file from the GUI
    # - load actions and execute them from CLI
    executor.register_actions(action_model.actions())

    # trash file destination is computed to include the original root name
    trash_root = cfg.act.execution.trash_root

    def source_path(root_name: str, root_relative: str) -> Path:
        candidate = data_dir.joinpath(root_relative)
        if candidate.exists():
            return candidate
        return data_dir.joinpath(root_name).joinpath(root_relative)

    def trash_path(root_name: str, root_relative: str) -> Path:
        rel_path = Path(root_relative)
        if rel_path.parts and rel_path.parts[0] == root_name:
            rel_path = Path(*rel_path.parts[1:])
        return trash_root.joinpath(root_name).joinpath(rel_path)

    def assert_in_dir(files: List[tuple[str, str]]):
        for root_name, rel in files:
            orig = source_path(root_name, rel)
            assert orig.exists(), orig

    def assert_not_in_dir(files: List[tuple[str, str]]):
        for root_name, rel in files:
            orig = source_path(root_name, rel)
            assert not orig.exists(), orig

    def assert_in_trash(files: List[tuple[str, str]]):
        for root_name, rel in files:
            dst = trash_path(root_name, rel)
            assert dst.exists(), dst

    def assert_not_in_trash(files: List[tuple[str, str]]):
        for root_name, rel in files:
            dst = trash_path(root_name, rel)
            assert not dst.exists(), dst

    assert_in_dir(deleted_files)
    assert_not_in_trash(deleted_files)

    executed_count = executor.execute_pending()
    assert executed_count == len(action_model.actions())

    done_action_count = 0
    with Session(executor.engine) as session:
        row: OperationRow
        for row in session.scalars(
                select(OperationRow).where(OperationRow.status == _DONE_STR)):
            done_action_count += 1
            trash_act = model_from_json_data(row.action_data, TrashAction)
            assert TrivialDataColumnSpec.column_name in trash_act.file.columns

    assert done_action_count == len(deleted_files)
    assert done_action_count == executed_count

    assert_not_in_dir(deleted_files)
    assert_in_trash(deleted_files)

    executed_count = executor.revert_done()
    assert executed_count == len(action_model.actions())

    assert_in_dir(deleted_files)
    assert_not_in_trash(deleted_files)


@beartype
def run_generated_directory_write(
        directories: list[GeneratedDirectory], stable_test_dir: Path,
        root_names: list[str]
) -> list[tuple[str, GeneratedDirectory, MaterializedDirectory]]:
    data_dir = stable_test_dir / "data"
    materialized_roots: list[tuple[str, GeneratedDirectory, MaterializedDirectory]] = []

    for root_name, directory in zip(root_names, directories):
        root_dir = data_dir / root_name
        materialized = write_generated_directory(root_dir, directory)
        assert_generated_directory_entries_exact(materialized.root, directory)

        assert len(materialized.files) == len(directory.files)
        assert len(list(root_dir.rglob("*"))) != 0

        for file_path in materialized.files:
            metadata_path = file_path.with_name(f"{file_path.name}.haxdex-meta.json")
            assert file_path.exists()
            assert metadata_path.exists()

        materialized_roots.append((root_name, directory, materialized))

    return materialized_roots


@beartype
def run_index_validation(
    index: IndexServiceConfig,
    directories: list[MaterializedDirectory],
):
    db = index.service.db
    file_documents = [it for it in db.aql.execute(AQL_FILE_PATHS)]
    file_paths: list[Path] = list(itertools.chain(*[d.files for d in directories]))
    assert len(file_documents) == len(file_paths)


@beartype
def run_file_tree_query_validation(
    tree: FileTreeServiceConfig,
    directories: list[MaterializedDirectory],
):
    db = tree.service.db
    assert tree.cfg.file_tree_view

    file_path_rows = _fetch_file_paths_impl(
        db, prepare_root_filters(tree.cfg.file_tree_view.root_dirs))

    file_paths: list[Path] = list(itertools.chain(*[d.files for d in directories]))

    file_paths = [f for f in file_paths if f.exists()]

    assert len(file_paths) == len(file_path_rows), pformat(
        dict(
            file_paths=file_paths,
            file_path_rows=[f.path for f in file_path_rows],
        ))


@beartype
def run_initial_index_collection(
    stable_test_dir: Path,
    root_names: list[str],
    materialized_roots: list[tuple[str, GeneratedDirectory, MaterializedDirectory]],
    directories: list[GeneratedDirectory],
):
    data_dir = stable_test_dir / "data"
    index = init_index_service(stable_test_dir, root_names=root_names)
    index.service.run_index()

    run_index_validation(index, [d for _, _, d in materialized_roots])

    assert materialized_roots
    assert data_dir == index.data_dir
    tree_config = init_file_tree_config(index)
    run_file_tree_query_validation(tree_config, [d for _, _, d in materialized_roots])

    assert tree_config.cfg.file_tree_view
    assert not tree_config.cfg.file_tree_view.visual_tree_cache_path.exists()
    assert not tree_config.cfg.file_tree_view.reference_tree_cache_path.exists()

    assert tree_config.cfg.file_tree_view

    reference_tree = FileTreeQueryCore.build_reference_tree(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
    )

    assert reference_tree is not None

    core = FileTreeQueryCore(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
        columns=init_file_tree_columns(index=index, reference_tree=reference_tree[0]),
    )

    # logger.info(simple_dump(core.model))
    # logger.info("\n" + render_text(model_dump.dump(core.model)))
    stable_test_dir.joinpath("simple_dump.txt").write_text(
        simple_dump_rows_json(core.model))

    m = core.model

    assert isinstance(m, FileTreeModel)
    assert len(m.nodes) == len(materialized_roots)
    assert m.rowCount() == len(materialized_roots)

    expected_entries: list[Path] = []
    expected_file_count = 0

    for root_name, directory, materialized in materialized_roots:
        direct_entries = [p.relative_path for p in directory.collect_entries_direct()]
        file_entries = list(
            f for f in materialized.root.glob("*") if not str(f).endswith(META_SUFFIX))
        assert len(direct_entries) == len(file_entries), pformat(
            dict(direct_entries=direct_entries, file_entries=file_entries))

        root_node = sub_row_by_name(QModelIndex(), m, root_name)
        assert root_node is not None
        assert m.rowCount(root_node) == len(directory.collect_entries_direct())

        for entry in directory.collect_files_direct():
            nested = sub_row_by_name(root_node, m, str(entry.relative_path))
            assert nested is not None
            assert m.rowCount(nested) == len(
                directory.collect_entries_direct(entry.relative_path))

        expected_entries.append(Path(root_name))
        for entry in directory.collect_entries_recursive():
            expected_entries.append(Path(root_name) / entry.relative_path)

        expected_file_count += len(directory.collect_files_recursive())

    table = TreeToTableProxyModel()
    table.setSourceModel(core.model)
    stable_test_dir.joinpath("table_model.txt").write_text(
        render_text(model_dump.dump(table)))
    rec_entries = expected_entries
    assert table.rowCount(QModelIndex()) == len(rec_entries)

    df = qt_model_to_dataframe(
        table,
        role=CustomModelRole.FullDataRole.value,
        role_names={
            CustomModelRole.FullDataRole.value: "data",
        },
    )

    df = run_main_model_splicing(
        df,
        stable_test_dir=stable_test_dir,
        expected_entries=rec_entries,
        core=core,
        gen_dir=data_dir,
        expected_file_count=expected_file_count,
        directories=directories,
    )

    run_video_file_filtering(
        df,
        core,
        stable_test_dir=stable_test_dir,
    )
    run_remove_duplicate_action(
        df,
        core,
        cfg=tree_config.cfg,
        data_dir=tree_config.data_dir,
    )


def run_action_column_validation(
    stable_test_dir: Path,
    root_names: list[str],
    materialized_roots: list[tuple[str, GeneratedDirectory, MaterializedDirectory]],
):
    logger.info(f"run action column validation root names {root_names}")
    index = init_index_service(stable_test_dir, root_names, reset=False)

    run_index_validation(index, [d for _, _, d in materialized_roots])

    tree_config = init_file_tree_config(index)
    run_file_tree_query_validation(tree_config, [d for _, _, d in materialized_roots])

    assert index.cfg.act
    assert index.cfg.act.execution.sqlite_path.exists(
    ), index.cfg.act.execution.sqlite_path
    columns = init_file_tree_columns(index=index)

    assert any(isinstance(c, KnownActionColumnSpec) for c in columns), [
        type(c) for c in columns
    ]

    core = FileTreeQueryCore(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
        columns=columns,
    )

    assert core.model.rowCount() == len(root_names)
    table = TreeToTableProxyModel()
    table.setSourceModel(core.model)

    assert 0 < table.rowCount()

    df = qt_model_to_dataframe(
        table,
        role=CustomModelRole.FullDataRole.value,
        role_names={
            CustomModelRole.FullDataRole.value: "data",
        },
    )

    assert 0 < len(df)
    df = split_columns_by_rules(df, _DF_SPLIT_RULES).sort_values("root_relative")

    assert 0 < len(df)
    stable_test_dir.joinpath("split_df_with_actions.json").write_text(
        format_json_with_fjson(
            to_json_safe(df),
            max_width=1200,
            max_inline_complexity=40,
            max_table_complexity=120,
        ))

    assert 0 < len(df[df["actions"].notna()])


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    phases=[Phase.generate],
    max_examples=1,  # 20
    deadline=5000,
)
@given(directories=st.lists(
    directory_structure(
        indexer_types=[],
        min_files=12,
        max_files=32,
        min_nesting=1,
        max_nesting=3,
        corpus_manifest=corpus_manifest,
        corpus_root=corpus_root,
        min_duplicates=2,
        max_duplicates=5,
    ),
    min_size=2,
    max_size=5,
))
@clean_test_dir
@capture_logs(test_name="main.log")
def test_generated_indexer_directory(
    stable_test_dir: Path,
    directories: list[GeneratedDirectory],
) -> None:
    logger.info(f"hypothesis example {current_build_context().data.index}")
    data_dir = stable_test_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    root_names = [f"root_{idx}" for idx in range(len(directories))]

    materialized_roots = run_generated_directory_write(
        directories=directories,
        stable_test_dir=stable_test_dir,
        root_names=root_names,
    )

    run_initial_index_collection(
        root_names=root_names,
        stable_test_dir=stable_test_dir,
        materialized_roots=materialized_roots,
        directories=directories,
    )

    run_action_column_validation(
        root_names=root_names,
        stable_test_dir=stable_test_dir,
        materialized_roots=materialized_roots,
    )


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    phases=[Phase.generate],
    max_examples=20,
    deadline=5000,
)
@given(directories=st.lists(
    directory_structure(
        indexer_types=[],
        min_files=12,
        max_files=32,
        min_nesting=1,
        max_nesting=3,
        corpus_manifest=corpus_manifest,
        corpus_root=corpus_root,
        min_duplicates=2,
        max_duplicates=5,
    ),
    min_size=2,
    max_size=5,
))
@clean_test_dir
@capture_logs(test_name="main.log")
def test_cli_index_rerun(
    stable_test_dir: Path,
    directories: list[GeneratedDirectory],
) -> None:
    logger.info(f"hypothesis example {current_build_context().data.index}")
    data_dir = stable_test_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    root_names = [f"root_{idx}" for idx in range(len(directories))]

    materialized_roots = run_generated_directory_write(
        directories=directories,
        stable_test_dir=stable_test_dir,
        root_names=root_names,
    )

    cfg = AppConfig(
        index=IndexConfig(
            paths=[
                IndexPathConfig(
                    name=m.root.name,
                    root_path=m.root,
                    paths=[DirConfig(path=m.root)],
                ) for _, _, m in materialized_roots
            ],
            reset=False,
        ),
        indexers={
            FFProbeIndexer.asset_name: FFProbeIndexer.config_model(),
            ExifMetadataIndexer.asset_name: ExifMetadataIndexer.config_model(),
            FileStatsIndexer.asset_name: FileStatsIndexer.config_model(),
            FileMimeIndexer.asset_name: FileMimeIndexer.config_model(),
            DocumentBlockIndexer.asset_name: DocumentBlockIndexer.config_model(),
        },
        resources={},
        index_cache=stable_test_dir.joinpath("index_cache.sqlite"),
        hash_cache=stable_test_dir.joinpath("hash_cache.sqlite"),
        db=DatabaseConfig(db_name=f"service_{stable_test_dir.stem}"),
        action_file=stable_test_dir.joinpath("actions.jsonl"),
        logging=LoggingConfig(setup_handlers=False),
    )

    IndexService.reset_for_config(cfg)

    with ExitStack() as stack:
        hash_cache_calculate = stack.enter_context(
            patch.object(
                HashCache,
                "_calculate",
                autospec=True,
                side_effect=HashCache._calculate,
            ))

        indexer_run_spies = {
            "ffprobe":
                stack.enter_context(
                    patch.object(
                        FFProbeIndexer,
                        "run",
                        autospec=True,
                        side_effect=FFProbeIndexer.run,
                    )),
            "exif":
                stack.enter_context(
                    patch.object(
                        ExifMetadataIndexer,
                        "run",
                        autospec=True,
                        side_effect=ExifMetadataIndexer.run,
                    )),
            "file_stats":
                stack.enter_context(
                    patch.object(
                        FileStatsIndexer,
                        "run",
                        autospec=True,
                        side_effect=FileStatsIndexer.run,
                    )),
            "file_mime":
                stack.enter_context(
                    patch.object(
                        FileMimeIndexer,
                        "run",
                        autospec=True,
                        side_effect=FileMimeIndexer.run,
                    )),
            "doc_blocks":
                stack.enter_context(
                    patch.object(
                        DocumentBlockIndexer,
                        "run",
                        autospec=True,
                        side_effect=DocumentBlockIndexer.run,
                    )),
        }

        main_impl("index", cfg)

        hash_cache_1 = hash_cache_calculate.call_count
        indexer_calls_1 = {
            name: spy.call_count for name, spy in indexer_run_spies.items()
        }

        main_impl("index", cfg)

        assert hash_cache_1 == hash_cache_calculate.call_count, (
            "Second indexing run could not restore the hash cache properly")

        for name, spy in indexer_run_spies.items():
            assert indexer_calls_1[name] > 0, (
                f"{name}.run was not called in the first indexing run")
            assert spy.call_count == indexer_calls_1[name], (
                f"{name}.run was called during the second indexing run")
