from dataclasses import dataclass
from enum import StrEnum

from beartype.typing import Annotated, Literal
from pydantic import BaseModel, Field


class SegmentKind(StrEnum):
    CLANG = "clang"
    TEXT = "text"


class MessagePartKind(StrEnum):
    TEXT = "text"
    TYPE = "type"


class Severity(StrEnum):
    ERROR = "error"
    FATAL_ERROR = "fatal error"
    WARNING = "warning"
    REMARK = "remark"
    NOTE = "note"


class NoteKind(StrEnum):
    INSTANTIATION = "instantiation"
    SUBSTITUTION = "substitution"
    CANDIDATE = "candidate"
    MACRO_EXPANSION = "macro_expansion"
    CONTEXT = "context"


class TokenKind(StrEnum):
    DIAGNOSTIC = "diagnostic"
    INCLUDE = "include"
    SOURCE = "source"
    SUMMARY = "summary"
    OTHER = "other"


class SourceLocation(BaseModel):
    file: str
    line: int | None = None
    column: int | None = None


class MessagePart(BaseModel):
    type: MessagePartKind
    text: str


class IncludeContext(BaseModel):
    log_line: int
    location: SourceLocation
    text: str


class DiagnosticMessage(BaseModel):
    log_line: int
    location: SourceLocation | None = None
    severity: Severity
    message: list[MessagePart]
    source: list[str] = Field(default_factory=list)


class DiagnosticNote(DiagnosticMessage):
    kind: NoteKind


class Diagnostic(DiagnosticMessage):
    include_context: list[IncludeContext] = Field(default_factory=list)
    notes: list[DiagnosticNote] = Field(default_factory=list)


class TextSegment(BaseModel):
    kind: Literal[SegmentKind.TEXT] = SegmentKind.TEXT
    start_line: int
    end_line: int
    text: str


class ClangSegment(BaseModel):
    kind: Literal[SegmentKind.CLANG] = SegmentKind.CLANG
    start_line: int
    end_line: int
    text: str
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    summaries: list[str] = Field(default_factory=list)
    unattached_include_context: list[IncludeContext] = Field(
        default_factory=list,
        description="Include lines not yet associated with a diagnostic.",
    )


class CompileLog(BaseModel):
    segments: list[
        Annotated[
            ClangSegment | TextSegment,
            Field(discriminator="kind"),
        ]
    ] = Field(default_factory=list)


@dataclass
class LogToken:
    kind: TokenKind
    log_line: int
    raw: str
    location: SourceLocation | None = None
    severity: Severity | None = None
    message: str = ""
