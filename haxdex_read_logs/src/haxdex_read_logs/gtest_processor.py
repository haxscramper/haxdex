import re

from beartype import beartype
from haxdex_read_logs.base_processor import (
    GOOGLE_TEST_MARKER,
    EventKind,
    EventLine,
    Island,
    LogProcessor,
    compose_islands,
    format_nested,
    make_template,
    scan_lines,
)
from haxdex_read_logs.clang_processor import ClangProcessor
from haxdex_read_logs.clang_type_model import SourceLocation
from haxdex_read_logs.cpptrace_processor import CpptraceProcessor
from haxdex_read_logs.exception_processor import ExceptionProcessor
from haxdex_read_logs.top_types import (
    AssertionKind,
    AssertionSegment,
    BlockBoundary,
    GoogleTestBlockKind,
    GoogleTestMarker,
    GoogleTestMarkerKind,
    GoogleTestSegment,
    GoogleTestStatus,
)
from rich.text import Text

GTEST_TEMPLATE = make_template(
    {
        EventKind.RUN_START: (
            r"(\[==========\] Running \d+ tests? "
            r"from \d+ test suites?\..*)"
        ),
        EventKind.RUN_END: (
            r"(\[==========\] \d+ tests? from "
            r"\d+ test suites? ran\..*)"
        ),
        EventKind.SUITE_END: (
            r"(\[----------\] \d+ tests? from .+ "
            r"\(\d+(?:\.\d+)? ms total\)\s*)"
        ),
        EventKind.SUITE_START: (r"(\[----------\] \d+ tests? from .+)"),
        EventKind.TEST_START: r"(\[\s*RUN\s*\]\s+.+)",
        EventKind.TEST_RESULT: (r"(\[\s*(?:OK|FAILED|SKIPPED)\s*\]\s+.+)"),
        EventKind.CONTROL: (
            r"(\[(?:==========|----------|\s*PASSED\s*|"
            r"\s*DISABLED\s*)\].*|Note: Google Test.*)"
        ),
    }
)

ASSERTION_TEMPLATE = make_template(
    {
        EventKind.ASSERTION: (
            r"((?:.+:\d+(?::\d+)?|unknown file): "
            r"(?:Failure|Skipped)\s*)"
        ),
    }
)
ASSERTION_PATTERN = re.compile(
    r"^(?:(?P<file>.+?):(?P<line>\d+)"
    r"(?::(?P<column>\d+))?|unknown file): "
    r"(?P<kind>Failure|Skipped)\s*$"
)
TEST_NAME_PATTERN = re.compile(
    r"^\[\s*(?:RUN|OK|FAILED|SKIPPED)\s*\]\s+"
    r"(?P<name>\S+)"
)
TEST_RESULT_PATTERN = re.compile(r"^\[\s*(?P<status>OK|FAILED|SKIPPED)\s*\]")
DURATION_PATTERN = re.compile(r"\((?P<duration>\d+(?:\.\d+)?) ms\)")
RUN_COUNTS_PATTERN = re.compile(
    r"Running (?P<tests>\d+) tests? from "
    r"(?P<suites>\d+) test suites?"
)
SUITE_PATTERN = re.compile(
    r"^\[----------\] (?P<count>\d+) tests? from "
    r"(?P<name>.+?)(?: \(\d+(?:\.\d+)? ms total\))?$"
)


@beartype
def test_name(event: EventLine) -> str | None:
    match = TEST_NAME_PATTERN.match(event.clean)
    if match is None:
        return None
    name = match["name"].rstrip(",")
    return name if "." in name else None


@beartype
def block_kind(event: EventLine) -> GoogleTestBlockKind | None:
    match event.kind:
        case EventKind.RUN_START:
            return GoogleTestBlockKind.RUN
        case EventKind.SUITE_START:
            return GoogleTestBlockKind.SUITE
        case EventKind.TEST_START:
            return GoogleTestBlockKind.TEST
        case EventKind.TEST_RESULT if test_name(event) is not None:
            return GoogleTestBlockKind.TEST
        case (
            EventKind.RUN_END
            | EventKind.SUITE_END
            | EventKind.TEST_RESULT
            | EventKind.CONTROL
        ):
            return GoogleTestBlockKind.FRAGMENT
        case _:
            return None


@beartype
class AssertionProcessor(LogProcessor[AssertionSegment]):
    def find_islands(self, text: str) -> list[Island]:
        events = scan_lines(text, ASSERTION_TEMPLATE)
        result: list[Island] = []
        position = 0

        while position < len(events):
            if events[position].kind != EventKind.ASSERTION:
                position += 1
                continue

            stop = position + 1
            while stop < len(events):
                event = events[stop]
                if event.kind == EventKind.ASSERTION or GOOGLE_TEST_MARKER.match(
                    event.clean
                ):
                    break
                stop += 1

            result.append(
                Island(
                    start=position,
                    stop=stop,
                    boundary=BlockBoundary(
                        opening_seen=True,
                        closing_seen=stop < len(events),
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
    ) -> AssertionSegment:
        events = scan_lines(text, ASSERTION_TEMPLATE)
        match = ASSERTION_PATTERN.fullmatch(events[0].clean) if events else None
        if match is None:
            raise ValueError(
                f"Invalid Google Test assertion at log line "
                f"{start_line}; expected file:line: Failure or Skipped, "
                f"got {text.splitlines()[:1]!r}"
            )

        location: SourceLocation | None = None
        if match["file"] is not None:
            location = SourceLocation(
                file=match["file"],
                line=int(match["line"]),
                column=(int(match["column"]) if match["column"] else None),
            )

        return AssertionSegment(
            start_line=start_line,
            end_line=start_line + len(events) - 1,
            boundary=boundary or BlockBoundary(opening_seen=True),
            assertion_kind=AssertionKind(match["kind"]),
            location=location,
            nested=compose_islands(
                text,
                [
                    ExceptionProcessor(),
                    CpptraceProcessor(),
                    ClangProcessor(),
                ],
                start_line=start_line,
            ),
        )

    def format_structured(self, value: AssertionSegment) -> Text:
        result = format_nested(value.nested)
        result.highlight_regex(
            r"(?m)^.*: Failure\s*$",
            "bold red",
        )
        result.highlight_regex(
            r"(?m)^.*: Skipped\s*$",
            "yellow",
        )
        return result


@beartype
class GoogleTestProcessor(LogProcessor[GoogleTestSegment]):
    def __init__(
        self,
        allowed: tuple[GoogleTestBlockKind, ...] = (
            GoogleTestBlockKind.RUN,
            GoogleTestBlockKind.SUITE,
            GoogleTestBlockKind.TEST,
            GoogleTestBlockKind.FRAGMENT,
        ),
    ) -> None:
        self.allowed = allowed

    def find_islands(self, text: str) -> list[Island]:
        events = scan_lines(text, GTEST_TEMPLATE)
        result: list[Island] = []
        position = 0

        while position < len(events):
            event = events[position]
            category = block_kind(event)

            if category is None or category not in self.allowed:
                position += 1
                continue

            stop = position + 1
            closed = False
            opened = event.kind in (
                EventKind.RUN_START,
                EventKind.SUITE_START,
                EventKind.TEST_START,
            )

            match category:
                case GoogleTestBlockKind.FRAGMENT:
                    while stop < len(events):
                        next_event = events[stop]
                        if (
                            block_kind(next_event) == GoogleTestBlockKind.FRAGMENT
                            or not next_event.clean.strip()
                        ):
                            stop += 1
                        else:
                            break

                case GoogleTestBlockKind.TEST if not opened:
                    closed = True

                case _:
                    while stop < len(events):
                        next_event = events[stop]

                        match category:
                            case GoogleTestBlockKind.RUN:
                                closes = next_event.kind == EventKind.RUN_END
                                interrupts = next_event.kind == EventKind.RUN_START

                            case GoogleTestBlockKind.SUITE:
                                closes = next_event.kind == EventKind.SUITE_END
                                interrupts = next_event.kind in (
                                    EventKind.RUN_START,
                                    EventKind.RUN_END,
                                    EventKind.SUITE_START,
                                )

                            case GoogleTestBlockKind.TEST:
                                closes = (
                                    next_event.kind == EventKind.TEST_RESULT
                                    and test_name(next_event) == test_name(event)
                                )
                                interrupts = next_event.kind in (
                                    EventKind.RUN_START,
                                    EventKind.RUN_END,
                                    EventKind.SUITE_START,
                                    EventKind.SUITE_END,
                                    EventKind.TEST_START,
                                    EventKind.TEST_RESULT,
                                )

                            case _:
                                raise ValueError(
                                    f"Cannot discover Google Test block "
                                    f"{category!r} at line "
                                    f"{position + 1}"
                                )

                        if closes:
                            stop += 1
                            closed = True
                            break
                        if interrupts:
                            break
                        stop += 1

                    if category == GoogleTestBlockKind.RUN and closed:
                        while stop < len(events):
                            next_event = events[stop]
                            if (
                                next_event.kind
                                in (
                                    EventKind.CONTROL,
                                    EventKind.TEST_RESULT,
                                )
                                or not next_event.clean.strip()
                            ):
                                stop += 1
                            else:
                                break

            result.append(
                Island(
                    start=position,
                    stop=stop,
                    boundary=BlockBoundary(
                        opening_seen=opened,
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
    ) -> GoogleTestSegment:
        events = scan_lines(text, GTEST_TEMPLATE)
        category = block_kind(events[0]) if events else None

        if category is None:
            raise ValueError(
                f"Invalid Google Test block at log line "
                f"{start_line}; expected a Google Test marker, "
                f"got {text.splitlines()[:1]!r}"
            )

        first = events[0]
        name: str | None = None
        status: GoogleTestStatus | None = None
        duration_ms: float | None = None
        expected_tests: int | None = None
        expected_suites: int | None = None
        closed = False

        match category:
            case GoogleTestBlockKind.RUN:
                counts = RUN_COUNTS_PATTERN.search(first.clean)
                if counts is not None:
                    expected_tests = int(counts["tests"])
                    expected_suites = int(counts["suites"])
                closed = any(event.kind == EventKind.RUN_END for event in events)
                allowed = (
                    GoogleTestBlockKind.SUITE,
                    GoogleTestBlockKind.TEST,
                    GoogleTestBlockKind.FRAGMENT,
                )

            case GoogleTestBlockKind.SUITE:
                suite = SUITE_PATTERN.fullmatch(first.clean)
                if suite is not None:
                    name = suite["name"]
                    expected_tests = int(suite["count"])
                closed = any(event.kind == EventKind.SUITE_END for event in events)
                allowed = (
                    GoogleTestBlockKind.TEST,
                    GoogleTestBlockKind.FRAGMENT,
                )

            case GoogleTestBlockKind.TEST:
                name = test_name(first)
                for event in events:
                    if event.kind == EventKind.TEST_RESULT and test_name(event) == name:
                        result = TEST_RESULT_PATTERN.match(event.clean)
                        if result is not None:
                            status = GoogleTestStatus(result["status"])
                            closed = True

                        duration = DURATION_PATTERN.search(event.clean)
                        if duration is not None:
                            duration_ms = float(duration["duration"])
                allowed = ()

            case GoogleTestBlockKind.FRAGMENT:
                allowed = ()

        processors: list[LogProcessor] = []
        if allowed:
            processors.append(GoogleTestProcessor(allowed))

        processors.extend(
            [
                AssertionProcessor(),
                ExceptionProcessor(),
                CpptraceProcessor(),
                ClangProcessor(),
            ]
        )

        return GoogleTestSegment(
            start_line=start_line,
            end_line=start_line + len(events) - 1,
            boundary=boundary
            or BlockBoundary(
                opening_seen=first.kind
                in (
                    EventKind.RUN_START,
                    EventKind.SUITE_START,
                    EventKind.TEST_START,
                ),
                closing_seen=closed,
            ),
            block_kind=category,
            name=name,
            status=status,
            duration_ms=duration_ms,
            expected_tests=expected_tests,
            expected_suites=expected_suites,
            markers=[
                GoogleTestMarker(
                    kind=GoogleTestMarkerKind(event.kind.value),
                    log_line=start_line + event.index,
                    text=event.raw,
                )
                for event in events
                if event.kind != EventKind.OTHER
            ],
            nested=compose_islands(
                text,
                processors,
                start_line=start_line,
            ),
        )

    def format_structured(self, value: GoogleTestSegment) -> Text:
        result = format_nested(value.nested)
        result.highlight_regex(
            r"(?m)^\[\s*FAILED\s*\].*$",
            "bold red",
        )
        result.highlight_regex(
            r"(?m)^\[\s*(?:OK|PASSED)\s*\].*$",
            "green",
        )
        result.highlight_regex(
            r"(?m)^\[\s*SKIPPED\s*\].*$",
            "yellow",
        )
        result.highlight_regex(
            r"(?m)^\[\s*RUN\s*\].*$",
            "bold",
        )
        return result
