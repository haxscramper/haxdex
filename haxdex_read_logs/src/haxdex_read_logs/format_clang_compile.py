import re
from io import StringIO
from textwrap import dedent

from beartype import beartype
from haxdex_read_logs.clang_error_model import (
    ClangSegment,
    CompileLog,
    Diagnostic,
    DiagnosticMessage,
    DiagnosticNote,
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
    content = Text.from_ansi("".join(source)).plain.expandtabs(8)
    content = re.sub(
        r"^[ \t]*(?:\d+[ \t]*)?\| ?",
        "",
        content,
        flags=re.MULTILINE,
    )

    return Text(
        dedent(content).rstrip("\r\n"),
        no_wrap=True,
        overflow="ignore",
    )


@beartype
def render_instantiation_subject(parts: list[MessagePart]) -> Text:
    message = render_message_parts(parts)
    start: int | None = None
    end = 0
    offset = 0

    for part in parts:
        if part.type == MessagePartKind.TYPE:
            if start is None:
                start = offset
            end = offset + len(part.text)

        offset += len(part.text)

    if start is not None:
        return message[start:end]

    prefix = re.match(
        r"^in instantiation of "
        r"(?:(?:.+? template specialization)|"
        r"(?:member function)|(?:member class)|"
        r"(?:static data member)|(?:function)|(?:class))\s+",
        message.plain,
    )
    start = prefix.end() if prefix is not None else 0

    suffix = re.search(r"\s+requested here\s*$", message.plain)
    end = suffix.start() if suffix is not None else len(message)

    return message[start:end]


@beartype
def render_instantiation_stack(notes: list[DiagnosticNote]) -> Text:
    sources = [
        render_source(note.source).split("\n", allow_blank=True) for note in notes
    ]
    annotation_column = (
        max(
            (line.cell_len for source in sources for line in source),
            default=0,
        )
        + 4
    )

    subject_label = "instantiated as "
    location_label = "in "
    location_indent = len(subject_label) - len(location_label)
    result = Text(no_wrap=True, overflow="ignore")

    for note_index, (note, source) in enumerate(zip(notes, sources, strict=True)):
        if note_index:
            result.append("\n")

        subject = Text(subject_label, style="dim")
        subject.append_text(render_instantiation_subject(note.message))
        annotations = [subject]

        if note.location is not None:
            location = Text(
                f"{' ' * location_indent}{location_label}",
                style="dim",
            )
            location.append_text(render_location(note.location))
            annotations.append(location)

        for row_index in range(max(len(source), len(annotations))):
            if row_index:
                result.append("\n")

            source_width = 0

            if row_index < len(source):
                source_line = source[row_index]
                result.append_text(source_line)
                source_width = source_line.cell_len

            if row_index < len(annotations):
                result.append(" " * (annotation_column - source_width))
                result.append_text(annotations[row_index])

    return result


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

        if kind == NoteKind.INSTANTIATION:
            result.append("\n")
            result.append_text(indent_text(render_instantiation_stack(notes), 2))
            continue

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


from rich.terminal_theme import MONOKAI, TerminalTheme


@beartype
def format_compile_log_html(
    log: CompileLog,
    *,
    theme: TerminalTheme = MONOKAI,
) -> str:
    console = Console(
        file=StringIO(),
        record=True,
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        markup=False,
        highlight=False,
        soft_wrap=True,
    )

    console.print(
        Text.from_ansi(
            format_compile_log(log, color=True),
            no_wrap=True,
            overflow="ignore",
        ),
        end="",
    )

    return console.export_html(
        theme=theme,
        inline_styles=True,
        clear=False,
    )
