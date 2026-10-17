from pathlib import Path

from beartype.typing import cast
from haxdex_core.job_types import BaseIndexer, BaseIndexerConfig, RunContext
from haxdex_core.types import IndexerRequest
from pydantic import BaseModel
from sqlalchemy import Engine

from haxdex_read_media.resources.pdf.pdf_extractor import (
    PdfExtractor,
    PdfExtractorRequest,
    PdfExtractorResult,
    PdfPage,
)


class PdfIndexerResult(BaseModel, extra="forbid"):
    pages: list[PdfPage]


class PdfIndexer(BaseIndexer):
    asset_name = "pdf_pages"
    result_model = PdfIndexerResult
    required_resources = ("pdf_extractor",)

    def __init__(self, config: BaseIndexerConfig, database: Engine) -> None:
        super().__init__(config=config, database=database)

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, object],
        assets: dict[str, object],
    ):
        extractor: PdfExtractor = cast(PdfExtractor, resources["pdf_extractor"])
        result: PdfExtractorResult = extractor.handle(
            ctx, PdfExtractorRequest(path=str(request.file_ref.path))
        )
        return IndexerOutput(
            indexer_id=self.asset_name,
            result=PdfIndexerResult(pages=result.pages),
        )
