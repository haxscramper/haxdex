from pathlib import Path
from pprint import pformat
from beartype.typing import Iterable, Any

from PyQt6.QtCore import QModelIndex, QAbstractItemModel, Qt
from hypothesis import given, settings, HealthCheck, Phase
import pytest
import plumbum

from haxdex.gui.agnostic import model_dump
from haxdex.gui.agnostic.model_dump import render_text, simple_dump
from haxdex.gui.agnostic.tree_to_table_model import TreeToTableProxyModel
from haxdex.gui.common.qt_utils import qt_model_to_dataframe
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.services.indexers.file_stats import FileStatsIndexer
from tests.generation import directory_structure, GeneratedDirectory, write_generated_directory, \
    assert_generated_directory_entries_exact, GeneratedIndexerFile, META_SUFFIX, GeneratedIndexerEntry, _sorted_rel
from tests.utils import init_index_service, init_file_tree_config, init_file_tree_columns, sub_row_by_name
import logging

log = logging.getLogger(__name__)


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
    indexer_types=[FileStatsIndexer],
    min_files=2,
    max_files=16,
    min_nesting=1,
    max_nesting=4,
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
)
@given(directory=directory_structure(
    indexer_types=[FileStatsIndexer],
    min_files=2,
    max_files=8,
    min_nesting=1,
    max_nesting=4,
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
def test_generated_indexer_directory(
    stable_test_dir: Path,
    directory: GeneratedDirectory,
) -> None:
    log.info("run")
    import shutil
    if stable_test_dir.exists():
        shutil.rmtree(stable_test_dir)

    stable_test_dir.mkdir(parents=True, exist_ok=True)

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
    core = FileTreeQueryCore(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
        columns=init_file_tree_columns(index),
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

    df = qt_model_to_dataframe(table)
    rec_basenames = [e.name for e in rec_entries]

    assert set(rec_basenames) == (set(df["name"]) - {"data"}), pformat(
        dict(
            rec_basenames=set(rec_basenames),
            model_names=set(df["name"]),
            original_model=simple_dump(core.model, max_col=1),
            real_directory=plumbum.local["exa"].run(["--tree", str(gen_dir)]),
        ))

    log.info("\n" + str(df))

    assert False
