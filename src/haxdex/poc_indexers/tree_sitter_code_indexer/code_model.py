from dataclasses import dataclass, field

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IndexEntry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("entries.id"), nullable=True)

    kind: Mapped[str] = mapped_column(nullable=False)
    language: Mapped[str | None] = mapped_column(nullable=True)

    name: Mapped[str | None] = mapped_column(nullable=True)
    path: Mapped[str | None] = mapped_column(nullable=True)
    qualified_name: Mapped[str | None] = mapped_column(nullable=True)

    start_line: Mapped[int | None] = mapped_column(nullable=True)
    end_line: Mapped[int | None] = mapped_column(nullable=True)

    type_text: Mapped[str | None] = mapped_column(nullable=True)
    signature_text: Mapped[str | None] = mapped_column(nullable=True)
    signature_source: Mapped[str | None] = mapped_column(nullable=True)

    doc_brief: Mapped[str | None] = mapped_column(nullable=True)
    doc_full: Mapped[str | None] = mapped_column(nullable=True)


class FunctionArgument(Base):
    __tablename__ = "function_arguments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)

    name: Mapped[str | None] = mapped_column(nullable=True)
    type_text: Mapped[str | None] = mapped_column(nullable=True)
    default_value: Mapped[str | None] = mapped_column(nullable=True)
    kind: Mapped[str | None] = mapped_column(nullable=True)


@dataclass
class DocInfo:
    brief: str | None = None
    full: str | None = None


@dataclass
class ParsedArgument:
    name: str | None
    type_text: str | None = None
    default_value: str | None = None
    kind: str | None = None


@dataclass
class ParsedEntry:
    kind: str
    path: str
    name: str | None = None
    language: str | None = None
    qualified_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    doc: DocInfo | None = None
    type_text: str | None = None
    signature_text: str | None = None
    signature_source: str | None = None
    arguments: list[ParsedArgument] = field(default_factory=list)
    children: list["ParsedEntry"] = field(default_factory=list)
