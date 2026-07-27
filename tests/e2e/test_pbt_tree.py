import dataclasses
import functools
import json
import shutil
from collections import defaultdict
from contextlib import redirect_stderr
from pathlib import Path
from pprint import pformat

from beartype import beartype
from beartype.typing import Iterable, Any, Iterator

from PyQt6.QtCore import QModelIndex, QAbstractItemModel, Qt
from hypothesis import given, settings, HealthCheck, Phase
import pytest
import plumbum
import pandas as pd
from contextlib import contextmanager

from haxdex.gui.agnostic import model_dump
from haxdex.gui.agnostic.model_dump import render_text, simple_dump
from haxdex.gui.agnostic.tree_to_table_model import TreeToTableProxyModel
from haxdex.gui.common.qt_model_roles import CustomModelRole
from haxdex.gui.common.qt_utils import qt_model_to_dataframe
from haxdex.gui.file_tree.columns.file_mime_column import FileMimeData, FileMimeColumnSpec
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode
from haxdex.gui.file_tree.columns.trivial_data_column import TrivialEntryData, TrivialDataColumnSpec
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.gui.file_tree.query_filter import QueryFilterEvaluator, QueryProgram
from haxdex.services.indexers.file_stats import FileStatsIndexer
from haxdex.services.pydantic_utils import to_json_safe
from haxdex.services.utils import propagate_logger_level
from tests.generation import directory_structure, GeneratedDirectory, write_generated_directory, \
    assert_generated_directory_entries_exact, GeneratedIndexerFile, META_SUFFIX, GeneratedIndexerEntry, _sorted_rel, \
    create_default_persistent_corpus
from tests.utils import init_index_service, init_file_tree_config, init_file_tree_columns, sub_row_by_name
import logging
import glom

log = logging.getLogger(__name__)

corpus_root = Path("/tmp/haxdex_tests/pbt_corpus")
corpus_manifest = create_default_persistent_corpus(corpus_root)

pd.set_option("display.max_rows", None)
pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
pd.set_option("display.max_colwidth", None)
pd.set_option("display.colheader_justify", "left")


def left_align_formatters(df):
    widths = {
        column: max(len(str(column)), df[column].map(str).str.len().max())
        for column in df.columns
    }

    return [(lambda width: (lambda value: f"{str(value):<{width}}"))(widths[column])
            for column in df.columns]


def qt_tree_to_df(model: QAbstractItemModel) -> pd.DataFrame:
    table = TreeToTableProxyModel()
    table.setSourceModel(model)
    return qt_model_to_dataframe(model=table,
                                 role=CustomModelRole.FullDataRole.value,
                                 role_names={CustomModelRole.FullDataRole.value: "data"})


def fmt_df(df: pd.DataFrame) -> str:
    return df.to_string(justify="left", formatters=left_align_formatters(df))


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


def split_columns_by_rules(
    df: pd.DataFrame,
    rules: list[tuple[str, list[str | tuple[Any, str]]]],
) -> pd.DataFrame:
    out = df.copy()

    for col, fields in rules:
        mapped = out[col].map(_cell_to_dict)

        for field in fields:
            if isinstance(field, str):
                field_key, res_name = field, field

            else:
                field_key, res_name = field

            out[res_name] = mapped.map(lambda d: glom.glom(d, field_key, default=None))

    out = out.drop(columns=[col for col, _ in rules])

    return out


def _fs_content_files(root: Path) -> list[Path]:
    return _sorted_rel(
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not path.name.endswith(META_SUFFIX))


def _all_query_paths_from_files(files: list[Path]) -> list[Path]:
    result: set[Path] = {Path(".")}
    for rel in files:
        result.update(rel.parents)
    return _sorted_rel(result)


def _fs_files_direct(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(
        path.relative_to(root)
        for path in base.glob("*")
        if path.is_file() and not path.name.endswith(META_SUFFIX))


def _fs_files_recursive(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(
        path.relative_to(root)
        for path in base.rglob("*")
        if path.is_file() and not path.name.endswith(META_SUFFIX))


def _fs_directories_direct(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(path.relative_to(root) for path in base.glob("*") if path.is_dir())


def _fs_directories_recursive(root: Path, query: Path) -> list[Path]:
    base = root if query == Path(".") else root / query
    return _sorted_rel(
        path.relative_to(root) for path in base.rglob("*") if path.is_dir())


def _fs_entries_direct(root: Path, query: Path) -> list[Path]:
    return _sorted_rel([
        *_fs_files_direct(root, query),
        *_fs_directories_direct(root, query),
    ])


def _fs_entries_recursive(root: Path, query: Path) -> list[Path]:
    return _sorted_rel([
        *_fs_files_recursive(root, query),
        *_fs_directories_recursive(root, query),
    ])


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
            dict(query=query,
                 actual=sorted(actual_files_direct),
                 expected=sorted(expected_files_direct)))

        assert actual_files_direct == expected_files_direct

        expected_files_recursive = set(_fs_files_recursive(materialized.root, query))
        actual_files_recursive = _relset(directory.collect_files_recursive(query))
        assert len(actual_files_recursive) == len(expected_files_recursive), pformat_dir(
            dict(query=query,
                 actual=sorted(actual_files_recursive),
                 expected=sorted(expected_files_recursive)))
        assert actual_files_recursive == expected_files_recursive

        expected_dirs_direct = set(_fs_directories_direct(materialized.root, query))
        actual_dirs_direct = _relset(directory.collect_directories_direct(query))
        assert len(actual_dirs_direct) == len(expected_dirs_direct), pformat_dir(
            dict(query=query,
                 actual=sorted(actual_dirs_direct),
                 expected=sorted(expected_dirs_direct)))
        assert actual_dirs_direct == expected_dirs_direct

        expected_dirs_recursive = set(_fs_directories_recursive(materialized.root, query))
        actual_dirs_recursive = _relset(directory.collect_directories_recursive(query))
        assert len(actual_dirs_recursive) == len(expected_dirs_recursive), pformat_dir(
            dict(query=query,
                 actual=sorted(actual_dirs_recursive),
                 expected=sorted(expected_dirs_recursive)))
        assert actual_dirs_recursive == expected_dirs_recursive

        expected_entries_direct = set(_fs_entries_direct(materialized.root, query))
        actual_entries_direct = _relset(directory.collect_entries_direct(query))
        assert len(actual_entries_direct) == len(expected_entries_direct), pformat_dir(
            dict(query=query,
                 actual=sorted(actual_entries_direct),
                 expected=sorted(expected_entries_direct)))
        assert actual_entries_direct == expected_entries_direct

        expected_entries_recursive = set(_fs_entries_recursive(materialized.root, query))
        actual_entries_recursive = _relset(directory.collect_entries_recursive(query))
        assert len(actual_entries_recursive) == len(
            expected_entries_recursive), pformat_dir(
                dict(query=query,
                     actual=sorted(actual_entries_recursive),
                     expected=sorted(expected_entries_recursive)))
        assert actual_entries_recursive == expected_entries_recursive

    for rel in fs_files:
        assert directory.get_file_by_relative_name(rel).relative_path == rel

    with pytest.raises(KeyError):
        directory.get_file_by_relative_name(Path("__missing__") / "nope.txt")


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    phases=[Phase.generate],
    max_examples=1,
    deadline=2000,
)
@given(directory=directory_structure(
    indexer_types=[],
    min_files=8,
    max_files=32,
    min_nesting=1,
    max_nesting=3,
    corpus_manifest=corpus_manifest,
    corpus_root=corpus_root,
    min_duplicates=2,
    max_duplicates=5,
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
@clean_test_dir
@capture_logs(test_name="main.log")
def test_generated_indexer_directory(
    stable_test_dir: Path,
    directory: GeneratedDirectory,
) -> None:
    gen_dir = stable_test_dir / "data"
    materialized = write_generated_directory(gen_dir, directory)
    assert_generated_directory_entries_exact(materialized.root, directory)

    assert len(materialized.files) == len(directory.files)
    assert len(list(gen_dir.rglob("*"))) != 0

    for file_path in materialized.files:
        metadata_path = file_path.with_name(f"{file_path.name}.haxdex-meta.json")
        assert file_path.exists()
        assert metadata_path.exists()

    index = init_index_service(stable_test_dir)

    index.service.run_index()

    assert materialized.root == index.root_dir
    tree_config = init_file_tree_config(index)

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

    # log.info(simple_dump(core.model))
    # log.info("\n" + render_text(model_dump.dump(core.model)))

    tree_root = core.model.index(0, 0, QModelIndex())
    m = core.model

    direct_entries = [p.relative_path for p in directory.collect_entries_direct()]
    file_entries = list(f for f in gen_dir.glob("*") if not str(f).endswith(META_SUFFIX))
    assert len(direct_entries) == len(file_entries), pformat(
        dict(direct_entries=direct_entries, file_entries=file_entries))

    assert m.rowCount(tree_root) == len(directory.collect_entries_direct())
    for entry_idx, entry in enumerate(directory.collect_files_direct()):
        nested = sub_row_by_name(tree_root, str(entry.relative_path))
        assert nested is not None
        assert m.rowCount(nested) == len(
            directory.collect_entries_direct(entry.relative_path))

    table = TreeToTableProxyModel()
    table.setSourceModel(core.model)
    stable_test_dir.joinpath("table_model.txt").write_text(
        render_text(model_dump.dump(table)))
    rec_entries = [e.relative_path for e in directory.collect_entries_recursive()]
    # tree table also adds the root `data` directory as a row, the generated directory
    # does not.
    assert table.rowCount(QModelIndex()) == len(rec_entries) + 1, pformat(rec_entries)

    df = qt_model_to_dataframe(table,
                               role=CustomModelRole.FullDataRole.value,
                               role_names={
                                   CustomModelRole.FullDataRole.value: "data",
                               })

    assert "name" in df.columns, str(df.columns)

    rules = [("trivial", ["assets", "is_directory", "root", "root_relative"]),
             ("share", ["size_self", "size_parent"]), ("mime", ["mime_type"]),
             ("name", [("name", "entry_name")]),
             ("framerate", [("probe.fps", "video_framerate")]),
             ("bitrate", [("probe.bitrate_bps", "video_bitrate")]),
             ("video_resolution", [
                 ("probe.width", "video_width"),
                 ("probe.height", "video_height"),
             ]),
             ("file_duplicates", [
                 ("hash", "file_hash"),
                 ("matches", "duplicate_paths"),
                 ("duplicate_count", "rec_duplicate_count"),
                 ("total_count", "rec_total_count"),
             ])]
    stable_test_dir.joinpath("df_pre_split.json").write_text(
        json.dumps(to_json_safe(df), indent=2))
    log.info("base flat model:\n" + fmt_df(df))
    df = split_columns_by_rules(df, rules).sort_values("root_relative")
    stable_test_dir.joinpath("df_post_split.json").write_text(
        json.dumps(to_json_safe(df), indent=2))

    log.info("base spliced model:\n" + fmt_df(df))
    rec_basenames = [e.name for e in rec_entries]

    assert set(rec_basenames) == (set(df["entry_name"]) - {"data"}), pformat(
        dict(
            rec_basenames=set(rec_basenames),
            model_names=set(df["entry_name"]),
            original_model=simple_dump(core.model, max_col=1),
            real_directory=plumbum.local["exa"].run(["--tree", str(gen_dir)]),
        ))

    assert df["size_self"].notna().all(), "`size_self` contains None values"
    assert df["size_self"].ne(0).all(), "`size_self` contains zero values"
    assert (df["size_self"]
            <= df["size_parent"]).all(), "entry size cannot be larger than the parent"

    video_mask = df["mime_type"].str.startswith("video/", na=False)

    assert df.loc[video_mask, "video_framerate"].notna().all()
    assert df.loc[video_mask, "video_bitrate"].notna().all()
    assert df.loc[video_mask, "video_width"].notna().all()
    assert df.loc[video_mask, "video_height"].notna().all()

    assert len(df[~df["is_directory"]]) == len(directory.collect_files_recursive())

    row = df.loc[df["root_relative"].eq("")]
    assert len(row) == 1
    row = row.iloc[0]

    assert len(df[~df["is_directory"]]) == row["rec_total_count"]

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
            assert isinstance(trivial, TrivialDataColumnSpec.column_type)
            if trivial.is_directory:
                result.append(node)

            else:
                assert FileMimeColumnSpec.column_name in node.columns, str(
                    node.columns.keys())
                mime = node.columns[FileMimeColumnSpec.column_name]
                assert isinstance(mime, FileMimeColumnSpec.column_type) or mime is None
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
    assert nodes_visited + 1 == len(df)
    assert len(mime_types) == df["mime_type"].nunique()

    video_df = qt_tree_to_df(video_only_model)
    log.info("video df:\n" + fmt_df(video_df))

    @beartype
    def map_trivial(trivial: TrivialEntryData) -> bool:
        return not trivial.is_directory

    assert len(df[df["mime_type"].str.startswith("video/")]) == len(
        video_df[video_df["trivial"].map(map_trivial)])
