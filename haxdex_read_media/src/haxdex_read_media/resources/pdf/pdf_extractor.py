from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import pymupdf
from haxdex_core.job_types import BaseResource, BaseResourceConfig, RunContext
from haxdex_read_media.resources.pdf.docling_extractor import (
    DoclingExtractor,
    DoclingPage,
)
from haxdex_read_media.resources.pdf.mypdf_extractor import (
    MyPDFExtractor,
    MyPDFPage,
)
from loguru import logger
from pydantic import BaseModel, Field


class PdfExtractorRequest(BaseModel, extra="forbid"):
    path: str
    pages: str | None = None
    ocr_only: bool = False


PdfPage = Annotated[
    MyPDFPage | DoclingPage,
    Field(discriminator="extractor"),
]


class PdfPageGeometry(BaseModel, extra="forbid"):
    width_points: float
    height_points: float
    rotation: int


class PdfExtractorResult(BaseModel, extra="forbid"):
    page_count: int
    pdf_metadata: dict[str, Any]
    is_repaired: bool
    page_geometry: dict[int, PdfPageGeometry]
    pages: list[PdfPage]


class PdfExtractorConfig(BaseResourceConfig, extra="forbid"):
    llama_server_url: str = "http://localhost:8080"
    model_id: str = "ibm/granite-docling"
    dpi: int = 300
    request_timeout: int = 600


class PdfExtractor(BaseResource):
    resource_key = "pdf_extractor"
    config_model = PdfExtractorConfig

    def __init__(self, config: PdfExtractorConfig) -> None:
        self.mypdf_extractor = MyPDFExtractor()
        self.docling_extractor = DoclingExtractor(
            llama_server_url=config.llama_server_url,
            model_id=config.model_id,
            dpi=config.dpi,
            request_timeout=config.request_timeout,
        )

    @staticmethod
    def _parse_page_range(
        pages: str | None,
        max_pages: int,
    ) -> tuple[int, int]:
        if not pages:
            return 1, max_pages

        parts = pages.split("-", 1)
        first = int(parts[0])
        last = (int(parts[1]) if parts[1] else max_pages) if len(parts) == 2 else first

        if not 1 <= first <= last <= max_pages:
            raise ValueError(f"Invalid PDF page range: {pages}")

        return first, last

    def handle(
        self,
        ctx: RunContext,
        request: PdfExtractorRequest,
    ) -> PdfExtractorResult:
        pdf_path = Path(request.path)

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        with (
            pymupdf.open(pdf_path) as pdf,
            ctx.trace_scope("extract PDF", path=str(pdf_path)),
        ):
            page_count = pdf.page_count
            pdf_metadata = dict(pdf.metadata or {})
            is_repaired = pdf.is_repaired

            first_page, last_page = self._parse_page_range(
                request.pages,
                page_count,
            )

            logger.info(
                f"Processing PDF: {pdf_path.name} | Range: {first_page}-{last_page}"
            )

            pages: list[MyPDFPage | DoclingPage] = []
            page_geometry: dict[int, PdfPageGeometry] = {}
            server_checked = False

            for page_num in range(first_page, last_page + 1):
                source_page = pdf[page_num - 1]
                page_geometry[page_num] = PdfPageGeometry(
                    width_points=source_page.rect.width,
                    height_points=source_page.rect.height,
                    rotation=source_page.rotation,
                )

                if not request.ocr_only and MyPDFExtractor.page_has_selectable_text(
                    pdf, page_num
                ):
                    with ctx.trace_scope("mypdf page", page_num=page_num):
                        page = self.mypdf_extractor.extract_page(
                            pdf_path,
                            page_num,
                        )
                else:
                    if not server_checked:
                        self.docling_extractor.check_server()
                        server_checked = True

                    with ctx.trace_scope("docling page", page_num=page_num):
                        page = self.docling_extractor.extract_page(
                            pdf_path,
                            page_num,
                        )

                pages.append(page)

        return PdfExtractorResult(
            page_count=page_count,
            pdf_metadata=pdf_metadata,
            is_repaired=is_repaired,
            page_geometry=page_geometry,
            pages=pages,
        )
