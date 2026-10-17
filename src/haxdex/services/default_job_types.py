from haxdex_core.job_types import BaseIndexer, BaseResource
from haxdex_read_media.indexers.file_size import FileSizeIndexer
from haxdex_read_media.indexers.file_stats import FileStatsIndexer
from haxdex_read_media.resources.file_reverser import (
    FileReverserResource,
)

DEFAULT_RESOURCE_TYPES: list[type[BaseResource]] = [
    FileReverserResource,
]

DEFAULT_INDEXER_TYPES: list[type[BaseIndexer]] = [
    FileSizeIndexer,
    FileStatsIndexer,
]
