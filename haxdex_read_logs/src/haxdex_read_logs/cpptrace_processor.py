import re
from pathlib import Path

from beartype import beartype
from haxdex_read_logs.base_processor import (
    ANSI_ESCAPE,
    EXCEPTION_SUFFIX,
    EventKind,
    Island,
    LogProcessor,
    make_template,
    scan_lines,
)
from haxdex_read_logs.clang_simplify import simplify_types
from haxdex_read_logs.clang_type_model import SourceLocation
from haxdex_read_logs.top_types import (
    BlockBoundary,
    StackFrame,
    StackTraceSegment,
)
from rich.text import Text

TRACE_TEMPLATE = make_template(
    {
        EventKind.TRACE_HEADER: (r"(\s*Stack trace \(most recent call first\):\s*)"),
        EventKind.FRAME: (r"(\s*#\d+\s+0x[0-9a-fA-F]+.*)"),
    }
)

FRAME_PATTERN = re.compile(
    r"^\s*#(?P<index>\d+)\s+"
    r"(?P<address>0x[0-9a-fA-F]+)(?P<tail>.*)$"
)
LOCATION_PATTERN = re.compile(
    r"(?P<file>\S+?)"
    r"(?::(?P<line>\d+)(?::(?P<column>\d+))?)?"
    r"(?P<trailing>[ \t]+.*)?"
)


@beartype
def frame_column(frame: StackFrame) -> str:
    if frame.location is None:
        return frame.address

    label = Path(frame.location.file).name
    if frame.location.line is not None:
        label = f"{label}:{frame.location.line}"

    return label


@beartype
class CpptraceProcessor(LogProcessor[StackTraceSegment]):
    def find_islands(self, text: str) -> list[Island]:
        events = scan_lines(text, TRACE_TEMPLATE)
        result: list[Island] = []
        position = 0

        while position < len(events):
            event = events[position]
            if event.kind not in (
                EventKind.TRACE_HEADER,
                EventKind.FRAME,
            ):
                position += 1
                continue

            stop = position + 1

            if not EXCEPTION_SUFFIX.search(event.clean):
                while stop < len(events) and events[stop].kind == EventKind.FRAME:
                    closes_exception = EXCEPTION_SUFFIX.search(events[stop].clean)
                    stop += 1
                    if closes_exception:
                        break

            result.append(
                Island(
                    start=position,
                    stop=stop,
                    boundary=BlockBoundary(
                        opening_seen=(event.kind == EventKind.TRACE_HEADER),
                        closing_seen=(
                            stop < len(events)
                            or EXCEPTION_SUFFIX.search(events[stop - 1].clean)
                            is not None
                        ),
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
    ) -> StackTraceSegment:
        events = scan_lines(text, TRACE_TEMPLATE)
        frames: list[StackFrame] = []
        header: str | None = None

        for event in events:
            match event.kind:
                case EventKind.TRACE_HEADER:
                    if header is not None or frames:
                        raise ValueError(
                            f"Unexpected cpptrace header at log line "
                            f"{start_line + event.index}: "
                            f"{event.clean!r}; expected one trace block"
                        )
                    header = event.raw

                case EventKind.FRAME:
                    clean = EXCEPTION_SUFFIX.sub("", event.clean)
                    match = FRAME_PATTERN.fullmatch(clean)
                    if match is None:
                        raise ValueError(
                            f"Invalid cpptrace frame at log line "
                            f"{start_line + event.index}: {clean!r}; "
                            f"expected an index and hexadecimal address"
                        )

                    tail = match["tail"].strip()
                    symbol: str | None = None
                    location_text: str | None = None

                    if tail.startswith("in "):
                        symbol_text = tail[3:]
                        if " at " in symbol_text:
                            symbol, location_text = symbol_text.rsplit(" at ", 1)
                        else:
                            symbol = symbol_text
                    elif tail.startswith("at "):
                        location_text = tail[3:]
                    elif tail:
                        symbol = tail

                    location: SourceLocation | None = None
                    trailing_text = ""
                    if location_text:
                        location_match = LOCATION_PATTERN.fullmatch(location_text)
                        if location_match is None:
                            raise ValueError(
                                f"Invalid cpptrace location at log line "
                                f"{start_line + event.index}: "
                                f"{location_text!r}; expected a file "
                                f"optionally followed by line and column"
                            )

                        trailing_text = location_match["trailing"] or ""
                        location = SourceLocation(
                            file=location_match["file"],
                            line=(
                                int(location_match["line"])
                                if location_match["line"]
                                else None
                            ),
                            column=(
                                int(location_match["column"])
                                if location_match["column"]
                                else None
                            ),
                        )

                    frames.append(
                        StackFrame(
                            index=int(match["index"]),
                            log_line=start_line + event.index,
                            address=match["address"],
                            symbol=symbol,
                            location=location,
                            trailing_text=trailing_text,
                            text=event.raw,
                        )
                    )

                case _:
                    raise ValueError(
                        f"Unexpected text inside cpptrace block at "
                        f"log line {start_line + event.index}: "
                        f"{event.clean!r}"
                    )

        if not events:
            raise ValueError(
                f"Cannot parse an empty cpptrace block at log line {start_line}"
            )

        return StackTraceSegment(
            start_line=start_line,
            end_line=start_line + len(events) - 1,
            boundary=boundary
            or BlockBoundary(
                opening_seen=header is not None,
                closing_seen=EXCEPTION_SUFFIX.search(events[-1].clean) is not None,
            ),
            header=header,
            frames=frames,
            text=text,
        )

    def format_structured(
        self,
        value: StackTraceSegment,
        *,
        show_original: bool = False,
    ) -> Text:
        return self.format_group(
            [value],
            show_original=show_original,
        )

    def format_group(
        self,
        values: list[StackTraceSegment],
        *,
        show_original: bool = True,
    ) -> Text:

        rewritten = [value.model_copy(deep=True) for value in values]
        symbols = list(
            dict.fromkeys(
                frame.symbol
                for value in rewritten
                for frame in value.frames
                if frame.symbol is not None
            )
        )

        replacements: dict[str, str] = {}
        if symbols:
            replacements = dict(
                zip(
                    symbols,
                    simplify_types(symbols),
                    strict=True,
                )
            )

        original_symbols = {
            id(frame): frame.symbol for value in rewritten for frame in value.frames
        }

        for value in rewritten:
            for frame in value.frames:
                if frame.symbol is not None:
                    frame.symbol = replacements[frame.symbol]

        column_width = max(
            (len(frame_column(frame)) for value in rewritten for frame in value.frames),
            default=0,
        )
        index_width = max(
            (len(f"#{frame.index}") for value in rewritten for frame in value.frames),
            default=0,
        )

        result = Text()

        for value in rewritten:
            if value.header is not None:
                header = Text.from_ansi(value.header)
                header.stylize("bold yellow")
                result.append(header)

            for frame in value.frames:
                index = f"#{frame.index}"
                column = frame_column(frame)

                result.append(
                    f"{index:<{index_width}}",
                    style="cyan",
                )
                result.append("  ")
                result.append(
                    f"{column:<{column_width}}",
                    style=("yellow" if frame.location is not None else "dim"),
                )

                if frame.symbol:
                    result.append(" in ")
                    result.append(frame.symbol)

                if frame.location is not None:
                    location_text = frame.location.file
                    if frame.location.line is not None:
                        location_text = f"{location_text}:{frame.location.line}"
                    if frame.location.column is not None:
                        location_text = f"{location_text}:{frame.location.column}"

                    result.append(
                        f" in {location_text}",
                        style="grey50",
                    )

                result.append(frame.trailing_text)
                clean = ANSI_ESCAPE.sub("", frame.text)
                suffix = EXCEPTION_SUFFIX.search(clean)
                if suffix is not None:
                    result.append(suffix.group().rstrip("\r\n"))

                line_ending = frame.text[len(frame.text.rstrip("\r\n")) :]
                original_symbol = original_symbols[id(frame)]

                if show_original and original_symbol is not None:
                    result.append(line_ending or "\n")
                    symbol_offset = index_width + 2 + column_width + len(" in ")
                    result.append(" " * symbol_offset)
                    result.append(
                        original_symbol,
                        style="grey50",
                    )

                result.append(line_ending)

        return result
