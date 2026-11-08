import re

from beartype import beartype
from haxdex_read_logs.base_processor import (
    ANSI_ESCAPE,
    EXCEPTION_SUFFIX,
    GOOGLE_TEST_MARKER,
    EventKind,
    Island,
    LogProcessor,
    compose_islands,
    format_nested,
    make_template,
    scan_lines,
)
from haxdex_read_logs.cpptrace_processor import CpptraceProcessor
from haxdex_read_logs.top_types import (
    BlockBoundary,
    ExceptionSegment,
)
from rich.text import Text

EXCEPTION_TEMPLATE = make_template(
    {
        EventKind.EXCEPTION_START: (r'(\s*C\+\+ exception with description ".*)'),
    }
)
EXCEPTION_PREFIX = re.compile(r'^\s*C\+\+ exception with description "')
TRACE_START = re.compile(
    r"(?m)^\s*(?:Stack trace \(most recent call first\):|"
    r"#\d+\s+0x[0-9a-fA-F]+)"
)


@beartype
class ExceptionProcessor(LogProcessor[ExceptionSegment]):
    def find_islands(self, text: str) -> list[Island]:
        events = scan_lines(text, EXCEPTION_TEMPLATE)
        result: list[Island] = []
        position = 0

        while position < len(events):
            if events[position].kind != EventKind.EXCEPTION_START:
                position += 1
                continue

            stop = position
            closed = False

            while stop < len(events):
                event = events[stop]

                if position < stop and (
                    event.kind == EventKind.EXCEPTION_START
                    or GOOGLE_TEST_MARKER.match(event.clean)
                ):
                    break

                stop += 1
                if EXCEPTION_SUFFIX.search(event.clean):
                    closed = True
                    break

            result.append(
                Island(
                    start=position,
                    stop=stop,
                    boundary=BlockBoundary(
                        opening_seen=True,
                        closing_seen=closed,
                    ),
                )
            )
            position = stop

        return result

    def parse_structured(
        self,
        text: str,
        *,
        start_line: int = 1,
        boundary: BlockBoundary | None = None,
    ) -> ExceptionSegment:
        clean = ANSI_ESCAPE.sub("", text)
        if EXCEPTION_PREFIX.match(clean) is None:
            raise ValueError(
                f"Invalid exception block at log line {start_line}; "
                f'expected C++ exception with description ", '
                f"got {clean.splitlines()[:1]!r}"
            )

        description = EXCEPTION_PREFIX.sub("", clean, count=1)
        closed = EXCEPTION_SUFFIX.search(description) is not None
        description = EXCEPTION_SUFFIX.sub("", description)
        trace_start = TRACE_START.search(description)
        if trace_start is not None:
            description = description[: trace_start.start()]

        return ExceptionSegment(
            start_line=start_line,
            end_line=start_line + len(text.splitlines()) - 1,
            boundary=boundary
            or BlockBoundary(
                opening_seen=True,
                closing_seen=closed,
            ),
            description=description.rstrip(),
            nested=compose_islands(
                text,
                [CpptraceProcessor()],
                start_line=start_line,
            ),
        )

    def format_structured(self, value: ExceptionSegment) -> Text:
        result = format_nested(value.nested)
        result.highlight_regex(
            r"C\+\+ exception with description",
            "bold red",
        )
        return result
