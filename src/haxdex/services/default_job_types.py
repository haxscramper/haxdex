from haxdex.services.core.job_types import (
    BaseIndexer,
    BaseResource,
)
from haxdex.services.indexers.chunk_indexing.file_embedding import FileEmbeddingIndexer
from haxdex.services.indexers.file_size import FileSizeIndexer
from haxdex.services.indexers.file_stats import FileStatsIndexer
from haxdex.services.indexers.chunk_indexing.file_summary import FileSummaryIndexer
from haxdex.services.indexers.full_document.full_document import DocumentBlockIndexer
from haxdex.services.indexers.chunk_indexing.full_text import FullTextIndexer
from haxdex.services.resources.file_reverser import FileReverserResource
from haxdex.services.resources.text_summary import TextSummaryResource

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
