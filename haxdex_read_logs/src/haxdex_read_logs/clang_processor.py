import io
import re
from io import StringIO

import textfsm
from beartype import beartype
from haxdex_read_logs.base_processor import ANSI_ESCAPE, Island, LogProcessor
from haxdex_read_logs.clang_segment_format import (
    format_clang_segment_group,
)
from haxdex_read_logs.clang_type_model import (
    ClangSegment,
    Diagnostic,
    DiagnosticMessage,
    DiagnosticNote,
    IncludeContext,
    LogToken,
    MessagePart,
    MessagePartKind,
    NoteKind,
    Severity,
    SourceLocation,
    TokenKind,
)
from haxdex_read_logs.top_types import BlockBoundary, CompileLog, TextSegment
from rich.text import Text

TEXTFSM_TEMPLATE = r"""Value LOG_LINE (\d+)
Value RAW (.*)
Value FILE (.+?)
Value ROW (\d+)
Value COLUMN (\d+)
Value SEVERITY (fatal error|error|warning|remark|note)
Value MESSAGE (.*)
Value DRIVER ((?:\S*/)?clang(?:\+\+)?(?:-\d+)?|<[^>]+>)
Value INCLUDE_FILE (.+?)
Value INCLUDE_ROW (\d+)
Value INCLUDE_COLUMN (\d+)
Value SOURCE ((?:\s*\d+\s*\|.*|\s*\|.*|\s*[\^~]+.*))
Value SUMMARY ((?:\d+ (?:warnings?|errors?)(?: and )?)+ generated\.)

Start
  ^\d+\t${FILE}:${ROW}:${COLUMN}:(?:\{[^}]*\}:)*\s+${SEVERITY}: ${MESSAGE}$$ -> Continue
  ^\d+\t${FILE}:${ROW}:\s+${SEVERITY}: ${MESSAGE}$$ -> Continue
  ^\d+\t${DRIVER}:\s+${SEVERITY}: ${MESSAGE}$$ -> Continue
  ^\d+\t${SEVERITY}: ${MESSAGE}$$ -> Continue
  ^\d+\t(?:In file included from |\s+from )${INCLUDE_FILE}:${INCLUDE_ROW}:${INCLUDE_COLUMN}[:,]\s*$$ -> Continue
  ^\d+\t(?:In file included from |\s+from )${INCLUDE_FILE}:${INCLUDE_ROW}[:,]\s*$$ -> Continue
  ^\d+\t${SOURCE}$$ -> Continue
  ^\d+\t${SUMMARY}\s*$$ -> Continue
  ^${LOG_LINE}\t${RAW}$$ -> Record

EOF
"""


QUOTED_FRAGMENT = re.compile(r"(?<!\w)'[^'\n]*'|‘[^’\n]*’")


@beartype
def split_message(message: str) -> list[MessagePart]:
    parts: list[MessagePart] = []
    position = 0

    for match in QUOTED_FRAGMENT.finditer(message):
        if position < match.start():
            parts.append(
                MessagePart(
                    type=MessagePartKind.TEXT,
                    text=message[position : match.start()],
                )
            )

        parts.append(
            MessagePart(
                type=MessagePartKind.TYPE,
                text=match.group()[1:-1],
            )
        )
        position = match.end()

    if position < len(message):
        parts.append(
            MessagePart(
                type=MessagePartKind.TEXT,
                text=message[position:],
            )
        )

    return parts


@beartype
def classify_note(message: str) -> NoteKind:
    match message:
        case str() if message.startswith(
            ("in instantiation of", "while instantiating")
        ):
            return NoteKind.INSTANTIATION

        case str() if message.startswith(
            (
                "in substitution",
                "while substituting",
                "during template argument deduction",
            )
        ):
            return NoteKind.SUBSTITUTION

        case str() if message.startswith("candidate"):
            return NoteKind.CANDIDATE

        case str() if message.startswith(
            ("expanded from macro", "in expansion of macro")
        ):
            return NoteKind.MACRO_EXPANSION

        case _:
            return NoteKind.CONTEXT


@beartype
def tokenize_compile_log(
    text: str,
    *,
    start_line: int = 1,
) -> list[LogToken]:
    original_lines = text.splitlines(keepends=True)
    numbered_lines: list[str] = []

    for number, raw in enumerate(original_lines, start=start_line):
        clean = ANSI_ESCAPE.sub("", raw.rstrip("\r\n"))
        numbered_lines.append(f"{number}\t{clean}")

    parser = textfsm.TextFSM(io.StringIO(TEXTFSM_TEMPLATE))
    records = parser.ParseText("\n".join(numbered_lines))
    tokens: list[LogToken] = []

    for raw, values in zip(original_lines, records, strict=True):
        record = dict(zip(parser.header, values, strict=True))
        log_line = int(record["LOG_LINE"])

        if record["SEVERITY"]:
            location: SourceLocation | None = None

            if record["FILE"]:
                location = SourceLocation(
                    file=record["FILE"],
                    line=int(record["ROW"]),
                    column=(int(record["COLUMN"]) if record["COLUMN"] else None),
                )
            elif record["DRIVER"]:
                location = SourceLocation(file=record["DRIVER"])

            tokens.append(
                LogToken(
                    kind=TokenKind.DIAGNOSTIC,
                    log_line=log_line,
                    raw=raw,
                    location=location,
                    severity=Severity(record["SEVERITY"]),
                    message=record["MESSAGE"],
                )
            )
            continue

        if record["INCLUDE_FILE"]:
            tokens.append(
                LogToken(
                    kind=TokenKind.INCLUDE,
                    log_line=log_line,
                    raw=raw,
                    location=SourceLocation(
                        file=record["INCLUDE_FILE"],
                        line=int(record["INCLUDE_ROW"]),
                        column=(
                            int(record["INCLUDE_COLUMN"])
                            if record["INCLUDE_COLUMN"]
                            else None
                        ),
                    ),
                )
            )
            continue

        kind = TokenKind.OTHER

        if record["SOURCE"]:
            kind = TokenKind.SOURCE
        elif record["SUMMARY"]:
            kind = TokenKind.SUMMARY

        tokens.append(
            LogToken(
                kind=kind,
                log_line=log_line,
                raw=raw,
                message=record["SUMMARY"],
            )
        )

    return tokens


@beartype
def segment_clang_logs(
    text: str,
    *,
    start_line: int = 1,
) -> CompileLog:
    """Group Clang diagnostics, include context, notes, and annotations."""
    result = CompileLog()
    current: ClangSegment | TextSegment | None = None
    diagnostic: Diagnostic | None = None
    source_owner: DiagnosticMessage | None = None

    for token in tokenize_compile_log(text, start_line=start_line):
        belongs_to_clang = token.kind != TokenKind.OTHER

        if token.kind == TokenKind.SOURCE:
            belongs_to_clang = (
                isinstance(current, ClangSegment) and source_owner is not None
            )

        if token.kind == TokenKind.OTHER and not token.raw.strip():
            belongs_to_clang = isinstance(current, ClangSegment)

        if not belongs_to_clang:
            if not isinstance(current, TextSegment):
                current = TextSegment(
                    start_line=token.log_line,
                    end_line=token.log_line,
                    text="",
                )
                result.segments.append(current)

            current.text += token.raw
            current.end_line = token.log_line
            diagnostic = None
            source_owner = None
            continue

        if not isinstance(current, ClangSegment):
            current = ClangSegment(
                start_line=token.log_line,
                end_line=token.log_line,
                text="",
            )
            result.segments.append(current)
            diagnostic = None
            source_owner = None

        # current.text += token.raw
        current.end_line = token.log_line

        match token.kind:
            case TokenKind.INCLUDE:
                if token.location is None:
                    raise ValueError(
                        f"Include context at log line {token.log_line} "
                        f"has no parsed location: {token.raw!r}"
                    )

                current.unattached_include_context.append(
                    IncludeContext(
                        log_line=token.log_line,
                        location=token.location,
                        text=token.raw,
                    )
                )
                diagnostic = None
                source_owner = None

            case TokenKind.DIAGNOSTIC:
                if token.severity is None:
                    raise ValueError(
                        f"Diagnostic at log line {token.log_line} "
                        f"has no parsed severity: {token.raw!r}"
                    )

                if token.severity == Severity.NOTE and diagnostic is not None:
                    note = DiagnosticNote(
                        log_line=token.log_line,
                        location=token.location,
                        severity=token.severity,
                        message=split_message(token.message),
                        kind=classify_note(token.message),
                    )
                    diagnostic.notes.append(note)
                    source_owner = note
                else:
                    diagnostic = Diagnostic(
                        log_line=token.log_line,
                        location=token.location,
                        severity=token.severity,
                        message=split_message(token.message),
                        include_context=current.unattached_include_context,
                    )
                    current.unattached_include_context = []
                    current.diagnostics.append(diagnostic)
                    source_owner = diagnostic

            case TokenKind.SOURCE:
                if source_owner is None:
                    raise ValueError(
                        f"Source annotation at log line {token.log_line} "
                        f"has no associated diagnostic: {token.raw!r}"
                    )

                source_owner.source.append(token.raw)

            case TokenKind.SUMMARY:
                current.summaries.append(token.message)
                diagnostic = None
                source_owner = None

            case TokenKind.OTHER:
                pass

    return result


@beartype
class ClangProcessor(LogProcessor[ClangSegment]):
    def find_islands(self, text: str) -> list[Island]:
        return [
            Island(
                start=segment.start_line - 1,
                stop=segment.end_line,
            )
            for segment in segment_clang_logs(text).segments
            if isinstance(segment, ClangSegment)
        ]

    def parse_structured(
        self,
        text: str,
        *,
        start_line: int = 1,
        boundary: BlockBoundary | None = None,
    ) -> ClangSegment:
        log = segment_clang_logs(text, start_line=start_line)

        match log.segments:
            case [ClangSegment() as segment]:
                return segment
            case _:
                raise ValueError(
                    f"Expected one Clang block at log line "
                    f"{start_line}, but extraction produced "
                    f"{[segment.kind for segment in log.segments]!r}; "
                    f"island discovery must exclude non-Clang text"
                )

    def format_structured(self, value: ClangSegment) -> Text:
        return self.format_group([value])

    def format_group(self, values: list[ClangSegment]) -> Text:
        output = StringIO()
        format_clang_segment_group(
            values,
            output=output,
            color=True,
        )
        return Text.from_ansi(output.getvalue())
