from beartype import beartype
from beartype.typing import cast
from haxdex_read_logs.base_processor import (
    Island,
    LogProcessor,
    TextProcessor,
    compose_islands,
    format_nested,
)
from haxdex_read_logs.clang_processor import ClangProcessor
from haxdex_read_logs.clang_type_model import ClangSegment
from haxdex_read_logs.cpptrace_processor import CpptraceProcessor
from haxdex_read_logs.exception_processor import ExceptionProcessor
from haxdex_read_logs.gtest_processor import (
    AssertionProcessor,
    GoogleTestProcessor,
)
from haxdex_read_logs.top_types import (
    AssertionSegment,
    BlockBoundary,
    CompileLog,
    ExceptionSegment,
    GoogleTestSegment,
    LogSegment,
    StackTraceSegment,
    TextSegment,
)
from rich.text import Text


@beartype
class TopProcessor(LogProcessor[CompileLog]):
    def find_islands(self, text: str) -> list[Island]:
        if not text:
            return []
        return [Island(start=0, stop=len(text.splitlines()))]

    def parse_structured(
        self,
        text: str,
        *,
        start_line: int = 1,
        boundary: BlockBoundary | None = None,
    ) -> CompileLog:
        return compose_islands(
            text,
            [
                GoogleTestProcessor(),
                AssertionProcessor(),
                ExceptionProcessor(),
                CpptraceProcessor(),
                ClangProcessor(),
            ],
            start_line=start_line,
        )

    def format_structured(self, value: CompileLog) -> Text:
        return format_nested(value)


@beartype
def format_segment_group(segments: list[LogSegment]) -> Text:
    if not segments:
        return Text()

    match segments[0]:
        case TextSegment():
            return TextProcessor().format_group(cast(list[TextSegment], segments))
        case ClangSegment():
            return ClangProcessor().format_group(cast(list[ClangSegment], segments))
        case StackTraceSegment():
            return CpptraceProcessor().format_group(
                cast(list[StackTraceSegment], segments)
            )
        case ExceptionSegment():
            return ExceptionProcessor().format_group(
                cast(list[ExceptionSegment], segments)
            )
        case GoogleTestSegment():
            return GoogleTestProcessor().format_group(
                cast(list[GoogleTestSegment], segments)
            )
        case AssertionSegment():
            return AssertionProcessor().format_group(
                cast(list[AssertionSegment], segments)
            )
        case _:
            raise TypeError(
                f"No formatter registered for segment type {type(segments[0]).__name__}"
            )
