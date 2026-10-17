from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Sequence,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

FULL_DOCUMENT_SCHEMA = MetaData()

DOCUMENT_BLOCK_ID_SEQUENCE = Sequence(
    "document_block_id_sequence",
    metadata=FULL_DOCUMENT_SCHEMA,
)
DOCUMENT_FRAGMENT_ID_SEQUENCE = Sequence(
    "full_document_fragment_id_sequence",
    metadata=FULL_DOCUMENT_SCHEMA,
)


class FULL_DOCUMENT_BASE(DeclarativeBase):
    metadata = FULL_DOCUMENT_SCHEMA


class FullDocumentFile(FULL_DOCUMENT_BASE):
    __tablename__ = "document_block"

    hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    id: Mapped[int] = mapped_column(
        Integer,
        DOCUMENT_BLOCK_ID_SEQUENCE,
        server_default=DOCUMENT_BLOCK_ID_SEQUENCE.next_value(),
        primary_key=True,
    )


class DocumentFragment(FULL_DOCUMENT_BASE):
    __tablename__ = "full_document_fragment"

    id: Mapped[int] = mapped_column(
        Integer,
        DOCUMENT_FRAGMENT_ID_SEQUENCE,
        server_default=DOCUMENT_FRAGMENT_ID_SEQUENCE.next_value(),
        primary_key=True,
    )
    file_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("document_block.id"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    plaintext: Mapped[str] = mapped_column(Text, nullable=False)
    structured_element: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )


class DocumentNesting(FULL_DOCUMENT_BASE):
    __tablename__ = "full_document_nesting"
    __table_args__ = (
        Index(
            "ix_full_document_nesting_parent_order",
            "parent_id",
            "order",
        ),
    )

    nested_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("full_document_fragment.id"),
        primary_key=True,
    )
    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("full_document_fragment.id"),
        nullable=True,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
