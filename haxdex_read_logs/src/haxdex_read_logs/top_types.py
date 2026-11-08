from __future__ import annotations

from enum import StrEnum

from beartype.typing import Annotated, Literal
from haxdex_read_logs.clang_type_model import (
    ClangSegment,
    SegmentKind,
    SourceLocation,
)
from pydantic import BaseModel, Field


class BlockBoundary(BaseModel):
    """Record observed boundaries rather than assuming complete input."""

    opening_seen: bool = False
    closing_seen: bool = False


class TextSegment(BaseModel):
    kind: Literal[SegmentKind.TEXT] = SegmentKind.TEXT
    start_line: int
    end_line: int
    text: str


class StackFrame(BaseModel):
    index: int
    log_line: int
    address: str
    symbol: str | None = None
    location: SourceLocation | None = None
    trailing_text: str = ""
    text: str


class StackTraceSegment(BaseModel):
    kind: Literal[SegmentKind.CPPTRACE] = SegmentKind.CPPTRACE
    start_line: int
    end_line: int
    boundary: BlockBoundary = Field(default_factory=BlockBoundary)
    header: str | None = None
    frames: list[StackFrame] = Field(default_factory=list)
    text: str


class ExceptionSegment(BaseModel):
    kind: Literal[SegmentKind.EXCEPTION] = SegmentKind.EXCEPTION
    start_line: int
    end_line: int
    boundary: BlockBoundary = Field(default_factory=BlockBoundary)
    description: str
    nested: CompileLog


class GoogleTestBlockKind(StrEnum):
    RUN = "run"
    SUITE = "suite"
    TEST = "test"
    FRAGMENT = "fragment"


class GoogleTestStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class GoogleTestMarkerKind(StrEnum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    SUITE_START = "suite_start"
    SUITE_END = "suite_end"
    TEST_START = "test_start"
    TEST_RESULT = "test_result"
    CONTROL = "control"


class GoogleTestMarker(BaseModel):
    kind: GoogleTestMarkerKind
    log_line: int
    text: str


class GoogleTestSegment(BaseModel):
    kind: Literal[SegmentKind.GOOGLE_TEST] = SegmentKind.GOOGLE_TEST
    start_line: int
    end_line: int
    boundary: BlockBoundary = Field(default_factory=BlockBoundary)
    block_kind: GoogleTestBlockKind
    name: str | None = None
    status: GoogleTestStatus | None = None
    duration_ms: float | None = None
    expected_tests: int | None = None
    expected_suites: int | None = None
    markers: list[GoogleTestMarker] = Field(default_factory=list)
    nested: CompileLog


class AssertionKind(StrEnum):
    FAILURE = "Failure"
    SKIPPED = "Skipped"


class AssertionSegment(BaseModel):
    kind: Literal[SegmentKind.ASSERTION] = SegmentKind.ASSERTION
    start_line: int
    end_line: int
    boundary: BlockBoundary = Field(default_factory=BlockBoundary)
    assertion_kind: AssertionKind
    location: SourceLocation | None = None
    nested: CompileLog


type LogSegment = Annotated[
    ClangSegment
    | TextSegment
    | StackTraceSegment
    | ExceptionSegment
    | GoogleTestSegment
    | AssertionSegment,
    Field(discriminator="kind"),
]


class CompileLog(BaseModel):
    segments: list[LogSegment] = Field(default_factory=list)


ExceptionSegment.model_rebuild()
GoogleTestSegment.model_rebuild()
AssertionSegment.model_rebuild()
CompileLog.model_rebuild()
