from pathlib import Path

from PyQt6.QtCore import QModelIndex, QAbstractItemModel, Qt
from hypothesis import given, settings, HealthCheck, Phase
import pytest

from haxdex.gui.agnostic import model_dump
from haxdex.gui.agnostic.model_dump import render_text
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.services.indexers.file_stats import FileStatsIndexer
from tests.generation import directory_structure, GeneratedDirectory, write_generated_directory, \
    assert_generated_directory_entries_exact, GeneratedIndexerFile
from tests.utils import init_index_service, init_file_tree_config, init_file_tree_columns, sub_row_by_name
import logging

log = logging.getLogger(__name__)


def _fs_content_files(root: Path) -> list[Path]:
    return sorted(
        (path.relative_to(root)
         for path in root.rglob("*")
         if path.is_file() and not path.name.endswith(".haxdex-meta.json")),
        key=lambda p: (len(p.parts), str(p)),
    )


def _all_query_paths_from_files(files: list[Path]) -> list[Path]:
    result: set[Path] = {Path(".")}
    for rel in files:
        parent = rel.parent
        while True:
            result.add(parent)
            if parent == Path("."):
                break
            parent = parent.parent
    return sorted(result, key=lambda p: (len(p.parts), str(p)))


def _relset(items: list[GeneratedIndexerFile]) -> set[Path]:
    return {item.relative_path for item in items}


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture],)
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

    fs_files = _fs_content_files(materialized.root)
    assert set(fs_files) == {item.relative_path for item in directory.files}

    for path in _all_query_paths_from_files(fs_files):
        expected_files_direct = {rel for rel in fs_files if rel.parent == path}

        if path == Path("."):
            expected_files_recursive = set(fs_files)
        else:
            expected_files_recursive = {
                rel for rel in fs_files if rel.is_relative_to(path)
            }

        expected_dirs_direct = {
            rel for rel in fs_files
            if rel.parent != Path(".") and rel.parent.parent == path
        }

        if path == Path("."):
            expected_dirs_recursive = {rel for rel in fs_files if rel.parent != Path(".")}
        else:
            expected_dirs_recursive = {
                rel for rel in fs_files if rel.parent != Path(".") and
                rel.parent.is_relative_to(path) and rel.parent != path
            }

        assert _relset(directory.collect_files_direct(path)) == expected_files_direct
        assert _relset(
            directory.collect_files_recursive(path)) == expected_files_recursive
        assert _relset(directory.collect_directories_direct(path)) == expected_dirs_direct
        assert _relset(
            directory.collect_directories_recursive(path)) == expected_dirs_recursive

        assert _relset(directory.collect_entries_direct(path)) == (expected_dirs_direct |
                                                                   expected_files_direct)
        assert _relset(
            directory.collect_entries_recursive(path)) == (expected_dirs_recursive |
                                                           expected_files_recursive)

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
    core = FileTreeQueryCore(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
        columns=init_file_tree_columns(index),
    )

    # log.info(simple_dump(core.model))
    flat_tree = model_dump.dump(core.model)
    log.info("\n" + render_text(flat_tree))

    tree_root = core.model.index(0, 0, QModelIndex())
    m = core.model

    assert m.rowCount(tree_root) == len(directory.collect_entries_direct())
    for entry_idx, entry in enumerate(directory.collect_files_direct()):
        nested = sub_row_by_name(tree_root, str(entry.relative_path))
        assert nested is not None
        assert m.rowCount(nested) == len(
            directory.collect_entries_direct(entry.relative_path))

    assert False
