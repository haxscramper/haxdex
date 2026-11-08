from beartype.typing import Annotated, Literal
from haxdex_read_logs.clang_type_model import ClangSegment, SegmentKind
from pydantic import BaseModel, Field


class TextSegment(BaseModel):
    kind: Literal[SegmentKind.TEXT] = SegmentKind.TEXT
    start_line: int
    end_line: int
    text: str


class CompileLog(BaseModel):
    segments: list[
        Annotated[
            ClangSegment | TextSegment,
            Field(discriminator="kind"),
        ]
    ] = Field(default_factory=list)
