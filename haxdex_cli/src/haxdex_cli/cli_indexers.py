# indexers
from haxdex_read_media.indexers.comfy_input_indexer import ComfyInputIndexer
from haxdex_read_media.indexers.exif_metadata import ExifMetadataIndexer
from haxdex_read_media.indexers.ffprobe_indexer import FFProbeIndexer
from haxdex_read_media.indexers.full_document.full_document import DocumentBlockIndexer
from haxdex_read_media.indexers.image_generation import GenerationParamsIndexer
from haxdex_read_media.indexers.image_hash import ImageHashIndexer
from haxdex_read_media.indexers.mime_indexer import FileMimeIndexer
from haxdex_read_media.indexers.pdf_indexer import PdfIndexer
from haxdex_read_media.indexers.safetensor_indexer import SafetensorIndexer
from haxdex_read_media.indexers.wd_indexer import WdTagIndexer

# resources
from haxdex_read_media.resources.flm_server import FlmServerResource
from haxdex_read_media.resources.pdf.pdf_extractor import PdfExtractor
from haxdex_read_media.resources.wd_tagger import WdTagger

from haxdex.services.default_job_types import (
    DEFAULT_INDEXER_TYPES,
    DEFAULT_RESOURCE_TYPES,
)

CLI_INDEXER_TYPES = [t for t in DEFAULT_INDEXER_TYPES] + [
    WdTagIndexer,
    PdfIndexer,
    FFProbeIndexer,
    SafetensorIndexer,
    ExifMetadataIndexer,
    ComfyInputIndexer,
    ImageHashIndexer,
    FileMimeIndexer,
    GenerationParamsIndexer,
    DocumentBlockIndexer,
]

CLI_RESOURCE_TYPES = [t for t in DEFAULT_RESOURCE_TYPES] + [
    FlmServerResource,
    WdTagger,
    PdfExtractor,
]
