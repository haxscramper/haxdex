from __future__ import annotations

from pathlib import Path

from beartype.typing import Any, cast
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from sqlalchemy import (
    JSON,
    ForeignKey,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from haxdex_read_media.resources.pdf.docling_extractor import DoclingPage
from haxdex_read_media.resources.pdf.page_model import DocTag
from haxdex_read_media.resources.pdf.pdf_extractor import (
    PdfExtractor,
    PdfExtractorRequest,
)

PDF_SCHEMA = MetaData()


class PDF_BASE(DeclarativeBase):
    metadata = PDF_SCHEMA


class PdfFile(PDF_BASE):
    __tablename__ = "pdf_files"

    hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    page_count: Mapped[int] = mapped_column(nullable=False)
    pdf_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_repaired: Mapped[bool] = mapped_column(nullable=False)


class PdfPage(PDF_BASE):
    __tablename__ = "pdf_pages"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "page_number",
            name="uq_pdf_pages_document_page",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("pdf_files.id"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(nullable=False)
    extractor: Mapped[str] = mapped_column(String(16), nullable=False)

    width_points: Mapped[float] = mapped_column(nullable=False)
    height_points: Mapped[float] = mapped_column(nullable=False)
    rotation: Mapped[int] = mapped_column(nullable=False)

    raw_output: Mapped[str] = mapped_column(Text, nullable=False)

    image_width: Mapped[int | None] = mapped_column()
    image_height: Mapped[int | None] = mapped_column()
    docling_document: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True),
    )


class PdfFragmentColumns:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pdf_pages.id"),
        nullable=False,
        index=True,
    )
    tag_id: Mapped[str] = mapped_column(String, nullable=False)
    tag_name: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)

    bbox_x: Mapped[float | None] = mapped_column()
    bbox_y: Mapped[float | None] = mapped_column()
    bbox_width: Mapped[float | None] = mapped_column()
    bbox_height: Mapped[float | None] = mapped_column()

    text: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    user_removed: Mapped[bool] = mapped_column(nullable=False, default=False)
    user_edited_text: Mapped[str | None] = mapped_column(Text)
    user_tag_override: Mapped[str | None] = mapped_column(String)


class MyPdfFragment(PdfFragmentColumns, PDF_BASE):
    __tablename__ = "pdf_mypdf_fragments"
    __table_args__ = (
        UniqueConstraint(
            "page_id",
            "tag_id",
            name="uq_pdf_mypdf_fragments_page_tag",
        ),
    )

    nested_in_id: Mapped[int | None] = mapped_column(
        ForeignKey("pdf_mypdf_fragments.id"),
        index=True,
    )


class DoclingFragment(PdfFragmentColumns, PDF_BASE):
    __tablename__ = "pdf_docling_fragments"
    __table_args__ = (
        UniqueConstraint(
            "page_id",
            "tag_id",
            name="uq_pdf_docling_fragments_page_tag",
        ),
    )

    nested_in_id: Mapped[int | None] = mapped_column(
        ForeignKey("pdf_docling_fragments.id"),
        index=True,
    )
    image_blob: Mapped[bytes | None] = mapped_column(LargeBinary)


def _save_fragments[FragmentT: (MyPdfFragment, DoclingFragment)](
    session: Session,
    fragment_type: type[FragmentT],
    page_id: int,
    tags: list[DocTag],
) -> dict[str, FragmentT]:
    fragments: dict[str, FragmentT] = {}

    def insert_tags(
        nested_tags: list[DocTag],
        nested_in_id: int | None,
    ) -> None:
        rows: list[tuple[DocTag, FragmentT]] = []

        for position, tag in enumerate(nested_tags):
            bbox = tag.bbox
            fragment = fragment_type(
                page_id=page_id,
                nested_in_id=nested_in_id,
                tag_id=tag.id,
                tag_name=tag.tag_name,
                position=position,
                bbox_x=bbox.x if bbox is not None else None,
                bbox_y=bbox.y if bbox is not None else None,
                bbox_width=bbox.width if bbox is not None else None,
                bbox_height=bbox.height if bbox is not None else None,
                text=tag.text,
                attributes=tag.attributes,
                user_removed=tag.user_removed,
                user_edited_text=tag.user_edited_text,
                user_tag_override=tag.user_tag_override,
            )
            session.add(fragment)
            rows.append((tag, fragment))
            fragments[tag.id] = fragment

        if not rows:
            return

        session.flush()

        for tag, fragment in rows:
            insert_tags(tag.nested, fragment.id)

    insert_tags(tags, nested_in_id=None)
    return fragments


class PdfIndexer(BaseIndexer):
    asset_name = "pdf_pages"
    result_schema = PDF_SCHEMA
    table = PdfFile.__table__
    required_resources = ("pdf_extractor",)

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()

        with (
            self.database.connection() as connection,
            Session(bind=connection) as session,
        ):
            existing_id = session.scalar(
                select(PdfFile.id).where(PdfFile.hash == file_hash)
            )

            if existing_id is not None:
                return

        extractor = cast(PdfExtractor, resources["pdf_extractor"])
        result = extractor.handle(
            ctx,
            PdfExtractorRequest(
                path=str(ctx.get_path(request.file_ref)),
            ),
        )

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            existing_id = session.scalar(
                select(PdfFile.id).where(PdfFile.hash == file_hash)
            )

            if existing_id is not None:
                return

            document = PdfFile(
                hash=file_hash,
                page_count=result.page_count,
                pdf_metadata=result.pdf_metadata,
                is_repaired=result.is_repaired,
            )
            session.add(document)
            session.flush()

            for extracted_page in result.pages:
                geometry = result.page_geometry[extracted_page.page_number]

                if isinstance(extracted_page, DoclingPage):
                    raw_output = extracted_page.raw_docling_response
                    image_width = extracted_page.image_width
                    image_height = extracted_page.image_height
                    docling_document = extracted_page.document.export_to_dict()
                else:
                    raw_output = extracted_page.raw_chunk_json
                    image_width = None
                    image_height = None
                    docling_document = None

                page = PdfPage(
                    document_id=document.id,
                    page_number=extracted_page.page_number,
                    extractor=extracted_page.extractor,
                    width_points=geometry.width_points,
                    height_points=geometry.height_points,
                    rotation=geometry.rotation,
                    raw_output=raw_output,
                    image_width=image_width,
                    image_height=image_height,
                    docling_document=docling_document,
                )
                session.add(page)
                session.flush()

                if isinstance(extracted_page, DoclingPage):
                    fragments = _save_fragments(
                        session=session,
                        fragment_type=DoclingFragment,
                        page_id=page.id,
                        tags=extracted_page.spatial_tags,
                    )

                    for extracted_image in extracted_page.extracted_images:
                        fragments[
                            extracted_image.tag_id
                        ].image_blob = extracted_image.image_blob
                else:
                    _save_fragments(
                        session=session,
                        fragment_type=MyPdfFragment,
                        page_id=page.id,
                        tags=extracted_page.spatial_tags,
                    )

            session.flush()
