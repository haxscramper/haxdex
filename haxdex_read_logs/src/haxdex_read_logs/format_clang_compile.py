from io import StringIO

from beartype import beartype
from haxdex_read_logs.clang_error_model import (
    ClangSegment,
    CompileLog,
    Diagnostic,
    DiagnosticMessage,
    IncludeContext,
    MessagePart,
    MessagePartKind,
    NoteKind,
    Severity,
    SourceLocation,
    TextSegment,
)
from rich.console import Console
from rich.text import Text


@beartype
def severity_style(severity: Severity) -> str:
    match severity:
        case Severity.ERROR | Severity.FATAL_ERROR:
            return "bold red"
        case Severity.WARNING:
            return "bold yellow"
        case Severity.REMARK:
            return "bold cyan"
        case Severity.NOTE:
            return "bold blue"


@beartype
def render_location(location: SourceLocation) -> Text:
    result = Text(location.file, style="dim", overflow="fold")

    if location.line is not None:
        result.append(f":{location.line}")

    if location.column is not None:
        result.append(f":{location.column}")

    return result


@beartype
def render_message_parts(parts: list[MessagePart]) -> Text:
    result = Text(overflow="fold")

    for part in parts:
        match part.type:
            case MessagePartKind.TEXT:
                result.append(part.text)
            case MessagePartKind.TYPE:
                result.append(part.text, style="bold cyan")

    return result


@beartype
def render_source(source: list[str]) -> Text:
    # Preserve source indentation and caret alignment within each original line.
    return Text.from_ansi(
        "".join(source).rstrip("\r\n"),
        overflow="fold",
    )


@beartype
def note_group_title(kind: NoteKind) -> str:
    match kind:
        case NoteKind.CONTEXT:
            return "Context"
        case NoteKind.CANDIDATE:
            return "Candidates"
        case NoteKind.SUBSTITUTION:
            return "Template substitution"
        case NoteKind.INSTANTIATION:
            return "Instantiation stack · diagnostic outward"
        case NoteKind.MACRO_EXPANSION:
            return "Macro expansion"


@beartype
def indent_text(text: Text, amount: int) -> Text:
    result = Text(no_wrap=True, overflow="ignore")
    prefix = " " * amount

    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            result.append("\n")

        if line.plain:
            result.append(prefix)
            result.append_text(line)

    return result


@beartype
def render_diagnostic_message(
    diagnostic: DiagnosticMessage,
    *,
    show_severity: bool = True,
) -> Text:
    result = Text(no_wrap=True, overflow="ignore")

    if show_severity:
        result.append(
            f"{diagnostic.severity.value}: ",
            style=severity_style(diagnostic.severity),
        )

    result.append_text(render_message_parts(diagnostic.message))

    if diagnostic.location is not None:
        result.append("\n")
        result.append_text(render_location(diagnostic.location))

    if diagnostic.source:
        result.append("\n")
        result.append_text(indent_text(render_source(diagnostic.source), 2))

    return result


@beartype
def render_candidate(candidate: DiagnosticMessage) -> Text:
    result = Text(no_wrap=True, overflow="ignore")

    if candidate.source:
        result.append_text(indent_text(render_source(candidate.source), 2))
        result.append("\n")

    result.append_text(
        indent_text(
            render_message_parts(candidate.message),
            32,
        )
    )

    if candidate.location is not None:
        result.append("\n")
        result.append_text(indent_text(render_location(candidate.location), 36))

    return result


@beartype
def render_include_context(
    entries: list[IncludeContext],
    *,
    title: str,
) -> Text:
    result = Text(no_wrap=True, overflow="ignore")
    result.append(f"{title} ({len(entries)})", style="dim")

    for entry in entries:
        result.append("\n")
        result.append_text(
            indent_text(
                Text.from_ansi(
                    entry.text.rstrip("\r\n"),
                    style="dim",
                ),
                2,
            )
        )

    return result


@beartype
def render_diagnostic(diagnostic: Diagnostic) -> Text:
    result = render_diagnostic_message(diagnostic)

    group_order = (
        NoteKind.CONTEXT,
        NoteKind.CANDIDATE,
        NoteKind.SUBSTITUTION,
        NoteKind.INSTANTIATION,
        NoteKind.MACRO_EXPANSION,
    )

    for kind in group_order:
        notes = [note for note in diagnostic.notes if note.kind == kind]
        if not notes:
            continue

        result.append("\n\n")
        result.append(
            f"{note_group_title(kind)} ({len(notes)})",
            style="bold",
        )

        for index, note in enumerate(notes):
            result.append("\n\n" if index else "\n")

            match kind:
                case NoteKind.CANDIDATE:
                    result.append_text(render_candidate(note))

                case _:
                    result.append_text(
                        indent_text(
                            render_diagnostic_message(
                                note,
                                show_severity=note.severity != Severity.NOTE,
                            ),
                            2,
                        )
                    )

    if diagnostic.include_context:
        result.append("\n\n")
        result.append_text(
            render_include_context(
                diagnostic.include_context,
                title="Include context",
            )
        )

    return result


@beartype
def format_clang_segment(
    segment: ClangSegment,
    *,
    color: bool,
) -> str:
    if (
        not segment.diagnostics
        and not segment.unattached_include_context
        and not segment.summaries
    ):
        return segment.text

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
        markup=False,
        highlight=False,
        soft_wrap=True,
    )

    for index, diagnostic in enumerate(segment.diagnostics):
        if index:
            console.print()

        console.print(render_diagnostic(diagnostic))

    if segment.unattached_include_context:
        if segment.diagnostics:
            console.print()

        console.print(
            render_include_context(
                segment.unattached_include_context,
                title="Unattached include context",
            )
        )

    for summary in segment.summaries:
        console.print(
            Text.from_ansi(
                summary.rstrip("\r\n"),
                style="bold",
                no_wrap=True,
                overflow="ignore",
            )
        )

    return output.getvalue()


@beartype
def format_compile_log(
    log: CompileLog,
    *,
    color: bool = False,
) -> str:
    """Render Clang diagnostics without wrapping; preserve text segments verbatim."""
    output = StringIO()

    for segment in log.segments:
        match segment:
            case TextSegment():
                output.write(segment.text)

            case ClangSegment():
                output.write(
                    format_clang_segment(
                        segment,
                        color=color,
                    )
                )

    return output.getvalue()
