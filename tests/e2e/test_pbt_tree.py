from pathlib import Path

from hypothesis import given, settings, HealthCheck

from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryCore
from haxdex.services.indexers.file_stats import FileStatsIndexer
from tests.generation import directory_structure, GeneratedDirectory, write_generated_directory
from tests.utils import init_index_service, init_file_tree_config, init_file_tree_columns


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=2,
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