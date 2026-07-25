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


def _all_query_paths(directory: GeneratedDirectory) -> list[Path]:
    result: set[Path] = {Path(".")}
    for item in directory.files:
        parent = item.relative_path.parent
        while True:
            result.add(parent)
            if parent == Path("."):
                break
            parent = parent.parent
    return sorted(result, key=lambda p: (len(p.parts), str(p)))


def _paths(items: list[GeneratedIndexerFile]) -> list[Path]:
    return [item.relative_path for item in items]


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

    fs_files = {
        path.relative_to(materialized.root)
        for path in materialized.root.rglob("*")
        if path.is_file() and not path.name.endswith(".haxdex-meta.json")
    }
    model_files = {item.relative_path for item in directory.files}
    assert fs_files == model_files

    for path in _all_query_paths(directory):
        expected_files_direct = [
            item for item in directory.files if item.relative_path.parent == path
        ]
        expected_files_recursive = []
        for item in directory.files:
            if path == Path("."):
                expected_files_recursive.append(item)
                continue
            if item.relative_path.is_relative_to(path):
                expected_files_recursive.append(item)

        expected_dirs_direct = [
            item for item in directory.files if item.relative_path.parent != Path(".") and
            item.relative_path.parent.parent == path
        ]
        expected_dirs_recursive = []
        for item in directory.files:
            parent = item.relative_path.parent
            if parent == Path("."):
                continue
            if path == Path("."):
                expected_dirs_recursive.append(item)
                continue
            if parent.is_relative_to(path) and parent != path:
                expected_dirs_recursive.append(item)

        assert _paths(
            directory.collect_files_direct(path)) == _paths(expected_files_direct)
        assert _paths(
            directory.collect_files_recursive(path)) == _paths(expected_files_recursive)
        assert _paths(
            directory.collect_directories_direct(path)) == _paths(expected_dirs_direct)
        assert _paths(directory.collect_directories_recursive(path)) == _paths(
            expected_dirs_recursive)

        expected_entries_direct = expected_dirs_direct + expected_files_direct
        expected_entries_recursive = expected_dirs_recursive + expected_files_recursive

        assert _paths(
            directory.collect_entries_direct(path)) == _paths(expected_entries_direct)
        assert _paths(directory.collect_entries_recursive(path)) == _paths(
            expected_entries_recursive)

    for item in directory.files:
        assert directory.get_file_by_relative_name(item.relative_path) == item

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
