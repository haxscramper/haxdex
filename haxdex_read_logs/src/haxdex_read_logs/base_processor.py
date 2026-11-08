from __future__ import annotations

import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

import textfsm
from beartype import beartype
from haxdex_read_logs.top_types import (
    BlockBoundary,
    CompileLog,
    LogSegment,
    TextSegment,
)
from pydantic import BaseModel, Field
from rich.text import Text

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
EXCEPTION_SUFFIX = re.compile(r'" thrown in [^\n]*\.\s*$')
GOOGLE_TEST_MARKER = re.compile(r"^\s*\[\s*[A-Z_=-]+\s*\]")


class EventKind(StrEnum):
    OTHER = "other"
    TRACE_HEADER = "trace_header"
    FRAME = "frame"
    EXCEPTION_START = "exception_start"
    ASSERTION = "assertion"
    RUN_START = "run_start"
    RUN_END = "run_end"
    SUITE_START = "suite_start"
    SUITE_END = "suite_end"
    TEST_START = "test_start"
    TEST_RESULT = "test_result"
    CONTROL = "control"


@dataclass(frozen=True)
class EventLine:
    index: int
    raw: str
    clean: str
    kind: EventKind


class Island(BaseModel):
    """A zero-based, half-open interval within the supplied text."""

    start: int
    stop: int
    boundary: BlockBoundary = Field(default_factory=BlockBoundary)


@beartype
def make_template(patterns: dict[EventKind, str]) -> str:
    values = [
        r"Value LINE (\d+)",
        r"Value RAW (.*)",
        *[f"Value {kind.name} {pattern}" for kind, pattern in patterns.items()],
    ]
    rules = [f"  ^\\d+\\t${{{kind.name}}}$$ -> Continue" for kind in patterns]
    return "\n".join(
        [
            *values,
            "",
            "Start",
            *rules,
            r"  ^${LINE}\t${RAW}$$ -> Record",
            "",
            "EOF",
            "",
        ]
    )


@beartype
def scan_lines(text: str, template: str) -> list[EventLine]:
    """Classify every line; template field order resolves overlapping rules."""
    raw_lines = text.splitlines(keepends=True)
    clean_lines = [ANSI_ESCAPE.sub("", raw.rstrip("\r\n")) for raw in raw_lines]
    numbered = "\n".join(f"{index}\t{clean}" for index, clean in enumerate(clean_lines))
    parser = textfsm.TextFSM(io.StringIO(template))
    records = parser.ParseText(numbered)
    result: list[EventLine] = []

    for index, (raw, clean, values) in enumerate(
        zip(raw_lines, clean_lines, records, strict=True)
    ):
        record = dict(zip(parser.header, values, strict=True))
        parsed_index = int(record["LINE"])

        if parsed_index != index:
            raise ValueError(
                f"TextFSM returned line {parsed_index} at record {index}; "
                f"expected one ordered record per input line: {raw!r}"
            )

        kind = next(
            (
                EventKind(name.lower())
                for name in parser.header
                if name not in ("LINE", "RAW") and record[name]
            ),
            EventKind.OTHER,
        )
        result.append(
            EventLine(
                index=index,
                raw=raw,
                clean=clean,
                kind=kind,
            )
        )

    return result


@beartype
class LogProcessor[ModelT: BaseModel](ABC):
    @abstractmethod
    def find_islands(self, text: str) -> list[Island]: ...

    @abstractmethod
    def parse_structured(
        self,
        text: str,
        *,
        start_line: int = 1,
        boundary: BlockBoundary | None = None,
    ) -> ModelT: ...

    @abstractmethod
    def format_structured(self, value: ModelT) -> Text: ...

    def format_group(self, values: list[ModelT]) -> Text:
        result = Text()
        for value in values:
            result.append(self.format_structured(value))
        return result

    def simplify_string(self, text: str) -> Text:
        return self.format_structured(self.parse_structured(text))


@beartype
class TextProcessor(LogProcessor[TextSegment]):
    def find_islands(self, text: str) -> list[Island]:
        return []

    def parse_structured(
        self,
        text: str,
        *,
        start_line: int = 1,
        boundary: BlockBoundary | None = None,
    ) -> TextSegment:
        return TextSegment(
            start_line=start_line,
            end_line=start_line + len(text.splitlines()) - 1,
            text=text,
        )

    def format_structured(self, value: TextSegment) -> Text:
        return Text.from_ansi(value.text)


@beartype
def append_segment(
    segments: list[LogSegment],
    segment: LogSegment,
) -> None:
    match segment:
        case TextSegment() if segments and isinstance(segments[-1], TextSegment):
            previous = segments[-1]
            if previous.end_line + 1 == segment.start_line:
                previous.text += segment.text
                previous.end_line = segment.end_line
            else:
                segments.append(segment)
        case _:
            segments.append(segment)


@beartype
def compose_islands(
    text: str,
    processors: list[LogProcessor],
    *,
    start_line: int = 1,
) -> CompileLog:
    """Apply processors in ownership order, only to still-unclaimed text."""
    if not text:
        return CompileLog()

    segments: list[LogSegment] = [
        TextProcessor().parse_structured(text, start_line=start_line)
    ]

    for processor in processors:
        assembled: list[LogSegment] = []

        for segment in segments:
            if not isinstance(segment, TextSegment):
                assembled.append(segment)
                continue

            lines = segment.text.splitlines(keepends=True)
            position = 0

            for island in processor.find_islands(segment.text):
                if not (
                    position <= island.start
                    and island.start < island.stop
                    and island.stop <= len(lines)
                ):
                    raise ValueError(
                        f"{type(processor).__name__} returned interval "
                        f"[{island.start}, {island.stop}) after offset "
                        f"{position} for {len(lines)} lines beginning at "
                        f"log line {segment.start_line}; intervals must be "
                        f"ordered, nonempty, nonoverlapping, and in bounds"
                    )

                if position < island.start:
                    append_segment(
                        assembled,
                        TextProcessor().parse_structured(
                            "".join(lines[position : island.start]),
                            start_line=segment.start_line + position,
                        ),
                    )

                parsed = processor.parse_structured(
                    "".join(lines[island.start : island.stop]),
                    start_line=segment.start_line + island.start,
                    boundary=island.boundary,
                )
                append_segment(assembled, parsed)
                position = island.stop

            if position < len(lines):
                append_segment(
                    assembled,
                    TextProcessor().parse_structured(
                        "".join(lines[position:]),
                        start_line=segment.start_line + position,
                    ),
                )

        segments = assembled

    return CompileLog(segments=segments)


@beartype
def format_nested(log: CompileLog) -> Text:
    from haxdex_read_logs.format_log_general import format_compile_log

    return Text.from_ansi(format_compile_log(log, color=True))
