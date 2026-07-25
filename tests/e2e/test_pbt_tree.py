from pathlib import Path

from PyQt6.QtCore import QModelIndex, QAbstractItemModel, Qt
from hypothesis import given, settings, HealthCheck, Phase

from haxdex.gui.agnostic import model_dump
from haxdex.gui.agnostic.model_dump import render_text
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.services.indexers.file_stats import FileStatsIndexer
from tests.generation import directory_structure, GeneratedDirectory, write_generated_directory
from tests.utils import init_index_service, init_file_tree_config, init_file_tree_columns, sub_row_by_name
import logging

log = logging.getLogger(__name__)

from beartype import beartype
from beartype.typing import List, Tuple


@beartype
def simple_dump(model: QAbstractItemModel, indentation_step: str = "  ") -> str:

    @beartype
    def format_display_value(value: object) -> str:
        match value:
            case None:
                return "None"
            case str():
                return value
            case _:
                return str(value)

    lines: List[str] = []

    @beartype
    def traverse(parent_index: QModelIndex, path: List[Tuple[int, int]],
                 depth: int) -> None:
        row_count = model.rowCount(parent_index)
        column_count = model.columnCount(parent_index)
        for row in range(row_count):
            for col in range(column_count):
                nested_index = model.index(row, col, parent_index)
                nested_path = [*path, (row, col)]
                path_text = "".join(
                    f"[{path_row}.{path_col}]" for path_row, path_col in nested_path)
                display_value = model.data(nested_index, Qt.ItemDataRole.DisplayRole)
                lines.append(
                    f"{indentation_step * depth}{path_text} {format_display_value(display_value)}"
                )
                traverse(nested_index, nested_path, depth + 1)

    traverse(QModelIndex(), [], 0)
    return "\n".join(lines)


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

    assert len(materialized.files) == len(directory.files)

    assert len(list(gen_dir.rglob("*"))) != 0

    for file_path in materialized.files:
        metadata_path = file_path.with_name(f"{file_path.name}.haxdex-meta.json")
        assert file_path.exists()
        assert metadata_path.exists()

    index = init_index_service(stable_test_dir)
    index.service.run_index()

    tree_config = init_file_tree_config(index)

    assert tree_config.cfg.file_tree_view
    core = FileTreeQueryCore(
        ctx=tree_config.service.ctx,
        db=tree_config.service.db,
        cfg=tree_config.cfg,
        indexer_instances=tree_config.service.indexer_instances,
        columns=init_file_tree_columns(index),
    )

    log.info(simple_dump(core.model))
    flat_tree = model_dump.dump(core.model)
    log.info(render_text(flat_tree))

    tree_root = core.model.index(0, 0, QModelIndex())
    m = core.model

    assert m.rowCount(tree_root) == len(directory.collect_entries_direct())
    for entry_idx, entry in enumerate(directory.collect_files_direct()):
        nested = sub_row_by_name(tree_root, str(entry.relative_path))
        assert nested is not None
        assert m.rowCount(nested) == len(
            directory.collect_entries_direct(entry.relative_path))

    assert False
