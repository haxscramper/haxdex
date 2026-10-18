from dataclasses import dataclass

from haxdex_core.database import IndexerStore
from haxdex_core.db_utils import duck_id_autoincrement
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from loguru import logger
from pydantic import TypeAdapter
from sqlalchemy import (
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    declared_attr,
    mapped_column,
)

from haxdex_read_media.indexers.chunk_indexing.chunking import (
    ChunkConfig,
    Chunker,
    ChunkUnit,
)
from haxdex_read_media.indexers.full_document.full_document_tables import (
    DocumentFragment,
    DocumentNesting,
    FullDocumentFile,
)
from haxdex_read_media.indexers.full_document.full_document_types import (
    DocumentBlock,
)

FULL_TEXT_SCHEMA = MetaData()


class FULL_TEXT_BASE(DeclarativeBase):
    metadata = FULL_TEXT_SCHEMA


class FullTextFile(FULL_TEXT_BASE):
    __tablename__ = "full_text"

    hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )

    @declared_attr
    def id(cls) -> Mapped[int]:
        return duck_id_autoincrement(cls)


class FullTextChunk(FULL_TEXT_BASE):
    __tablename__ = "full_text_chunk"
    __table_args__ = (UniqueConstraint("file_id", "index"),)

    @declared_attr
    def id(cls) -> Mapped[int]:
        return duck_id_autoincrement(cls)

    file_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("full_text.id"),
        nullable=False,
        index=True,
    )
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)


class FullTextSpan(FULL_TEXT_BASE):
    __tablename__ = "full_text_span"

    chunk_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("full_text_chunk.id"),
        primary_key=True,
    )
    index: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_fragment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_end: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)


@dataclass(frozen=True)
class _BlockNesting:
    from_: str
    to_: str
    order: int


def _load_document(
    database: IndexerStore,
    file_hash: str,
) -> tuple[list[DocumentBlock], list[_BlockNesting]] | None:
    adapter = TypeAdapter(DocumentBlock)

    with database.connection() as connection, Session(bind=connection) as session:
        document_file = session.scalar(
            select(FullDocumentFile).where(
                FullDocumentFile.hash == file_hash,
            )
        )
        if document_file is None:
            return None

        rows = session.execute(
            select(DocumentFragment, DocumentNesting)
            .outerjoin(
                DocumentNesting,
                DocumentNesting.nested_id == DocumentFragment.id,
            )
            .where(DocumentFragment.file_id == document_file.id)
            .order_by(
                DocumentNesting.order,
                DocumentFragment.id,
            )
        )

        documents: list[DocumentBlock] = []
        edges: list[_BlockNesting] = []

        for fragment, nesting in rows:
            # The existing chunker uses string identifiers. Use the database
            # fragment ID rather than reconstructing a content hash.
            documents.append(
                adapter.validate_python(
                    {
                        **fragment.structured_element,
                        "hash": str(fragment.id),
                        "file_hash": file_hash,
                    }
                )
            )

            if nesting is not None and nesting.parent_id is not None:
                edges.append(
                    _BlockNesting(
                        from_=str(nesting.parent_id),
                        to_=str(nesting.nested_id),
                        order=nesting.order,
                    )
                )

        return documents, edges


class FullTextIndexer(BaseIndexer):
    asset_name = "full_text"
    result_schema = FULL_TEXT_SCHEMA
    table = FullTextFile.__table__
    required_assets = ("document_block",)

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            existing = session.scalar(
                select(FullTextFile).where(
                    FullTextFile.hash == file_hash,
                )
            )
            if existing is not None:
                return

            source = _load_document(assets["document_block"], file_hash)
            if source is None:
                chunks = []
            else:
                documents, edges = source
                chunker = Chunker(
                    ChunkConfig(
                        unit=ChunkUnit.CHARS,
                        max_size=2000,
                        min_size=500,
                        start_overlap_min=0,
                        start_overlap_max=200,
                    )
                )
                chunks = chunker.chunk_blocks(documents, edges, file_hash)

                if ctx.get_path(request.file_ref).stat().st_size > 0:
                    assert chunks, "Nonempty input produced no full-text chunks"

            logger.debug(f"created {len(chunks)} chunks")

            document_file = FullTextFile(hash=file_hash)
            session.add(document_file)
            session.flush()

            chunk_rows = [
                FullTextChunk(
                    file_id=document_file.id,
                    hash=chunk.hash,
                    index=chunk.index,
                    text=chunk.text,
                    char_count=chunk.char_count,
                    token_count=chunk.token_count,
                    unit=chunk.unit,
                )
                for chunk in chunks
            ]
            session.add_all(chunk_rows)
            session.flush()

            session.add_all(
                [
                    FullTextSpan(
                        chunk_id=chunk_row.id,
                        index=span_index,
                        source_fragment_id=int(span.source_hash),
                        source_start=span.source_start,
                        source_end=span.source_end,
                        chunk_start=span.chunk_start,
                        chunk_end=span.chunk_end,
                        kind=span.kind,
                    )
                    for chunk_row, chunk in zip(
                        chunk_rows,
                        chunks,
                        strict=True,
                    )
                    for span_index, span in enumerate(chunk.spans)
                ]
            )
            session.flush()
