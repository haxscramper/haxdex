from pathlib import Path

from hypothesis import given, settings, HealthCheck

from haxdex.services.indexers.file_stats import FileStatsIndexer
from tests.generation import directory_structure, GeneratedDirectory, write_generated_directory


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
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
    materialized = write_generated_directory(stable_test_dir, directory)

    assert len(materialized.files) == len(directory.files)

    for file_path in materialized.files:
        metadata_path = file_path.with_name(f"{file_path.name}.haxdex-meta.json")
        assert file_path.exists()
        assert metadata_path.exists()
