from haxdex.services.core.job_types import (
    BaseIndexer,
    BaseResource,
)
from haxdex_read_media.src.haxdex_read_media.indexers.chunk_indexing.file_embedding import (
    FileEmbeddingIndexer,
)
from haxdex_read_media.src.haxdex_read_media.indexers.chunk_indexing.file_summary import (
    FileSummaryIndexer,
)
from haxdex_read_media.src.haxdex_read_media.indexers.chunk_indexing.full_text import (
    FullTextIndexer,
)
from haxdex_read_media.src.haxdex_read_media.indexers.file_size import FileSizeIndexer
from haxdex_read_media.src.haxdex_read_media.indexers.file_stats import FileStatsIndexer
from haxdex_read_media.src.haxdex_read_media.indexers.full_document.full_document import (
    DocumentBlockIndexer,
)
from haxdex_read_media.src.haxdex_read_media.resources.file_reverser import (
    FileReverserResource,
)
from haxdex_read_media.src.haxdex_read_media.resources.text_summary import (
    TextSummaryResource,
)

DEFAULT_RESOURCE_TYPES: list[type[BaseResource]] = [
    FileReverserResource,
    TextSummaryResource,
]

DEFAULT_INDEXER_TYPES: list[type[BaseIndexer]] = [
    FileSizeIndexer,
    FileStatsIndexer,
    FullTextIndexer,
    FileSummaryIndexer,
    FileEmbeddingIndexer,
    DocumentBlockIndexer,
]
