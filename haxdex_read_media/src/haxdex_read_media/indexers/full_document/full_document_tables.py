from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, Integer, MetaData, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

FULL_DOCUMENT_SCHEMA = MetaData()


class FULL_DOCUMENT_BASE(DeclarativeBase):
    metadata = FULL_DOCUMENT_SCHEMA


class FullDocumentFile(FULL_DOCUMENT_BASE):
    __tablename__ = "document_block"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)


class DocumentFragment(FULL_DOCUMENT_BASE):
    __tablename__ = "full_document_fragment"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document_block.hash"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    plaintext: Mapped[str] = mapped_column(Text, nullable=False)
    structured_element: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class DocumentNesting(FULL_DOCUMENT_BASE):
    __tablename__ = "full_document_nesting"
    __table_args__ = (
        Index(
            "ix_full_document_nesting_file_parent_order",
            "file_hash",
            "parent_id",
            "order",
        ),
    )

    child_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("full_document_fragment.id"),
        primary_key=True,
    )
    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document_block.hash"),
        nullable=False,
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("full_document_fragment.id"),
        nullable=True,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
