import re
from io import StringIO
from textwrap import dedent

from beartype import beartype
from haxdex_read_logs.clang_type_model import (
    ClangSegment,
    Diagnostic,
    DiagnosticMessage,
    DiagnosticNote,
    IncludeContext,
    MessagePart,
    MessagePartKind,
    NoteKind,
    Severity,
    SourceLocation,
)
from rich.console import Console
from rich.text import Text
from rich.tree import Tree


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
def render_instantiation_frames(
    frames: list[list[DiagnosticNote]],
    *,
    annotation_column: int | None = None,
) -> Text:
    sources = [
        render_source(frame[0].source).split("\n", allow_blank=True)
        if frame[0].source
        else []
        for frame in frames
    ]

    if annotation_column is None:
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

    for frame_index, (frame, source) in enumerate(zip(frames, sources, strict=True)):
        if frame_index:
            result.append("\n")

        subjects: list[Text] = []
        seen_subjects: set[str] = set()

        for note in frame:
            subject = render_instantiation_subject(note.message)

            if subject.plain in seen_subjects:
                continue

            seen_subjects.add(subject.plain)
            subjects.append(subject)

        rows: list[tuple[Text | None, Text | None]] = []

        for subject_index, subject in enumerate(subjects):
            annotation = Text(
                subject_label if not subject_index else " " * len(subject_label),
                style="dim",
            )
            annotation.append_text(subject)
            rows.append(
                (
                    source[0] if source and not subject_index else None,
                    annotation,
                )
            )

        location: Text | None = None

        if frame[0].location is not None:
            location = Text(
                f"{' ' * location_indent}{location_label}",
                style="dim",
            )
            location.append_text(render_location(frame[0].location))

        remaining_source = source[1:]

        for row_index in range(
            max(len(remaining_source), 1 if location is not None else 0)
        ):
            rows.append(
                (
                    remaining_source[row_index]
                    if row_index < len(remaining_source)
                    else None,
                    location if not row_index else None,
                )
            )

        for row_index, (source_line, annotation) in enumerate(rows):
            if row_index:
                result.append("\n")

            source_width = 0

            if source_line is not None:
                result.append_text(source_line)
                source_width = source_line.cell_len

            if annotation is not None:
                result.append(" " * (annotation_column - source_width))
                result.append_text(annotation)

    return result


@beartype
def render_instantiation_stack(
    notes: list[DiagnosticNote],
    *,
    annotation_column: int | None = None,
) -> Text:
    return render_instantiation_frames(
        [[note] for note in notes],
        annotation_column=annotation_column,
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

    message = render_message_parts(candidate.message)

    if candidate.location is not None:
        message.append(" ")
        message.append_text(render_location(candidate.location))

    result.append_text(indent_text(message, 32))
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
def location_key(
    location: SourceLocation | None,
) -> tuple[str, int | None, int | None] | None:
    if location is None:
        return None

    return location.file, location.line, location.column


@beartype
def messages_equal(
    left: DiagnosticMessage,
    right: DiagnosticMessage,
) -> bool:
    return (
        left.severity == right.severity
        and location_key(left.location) == location_key(right.location)
        and [(part.type, part.text) for part in left.message]
        == [(part.type, part.text) for part in right.message]
        and left.source == right.source
    )


@beartype
def notes_equal(left: DiagnosticNote, right: DiagnosticNote) -> bool:
    return left.kind == right.kind and messages_equal(left, right)


@beartype
def diagnostics_equal(left: Diagnostic, right: Diagnostic) -> bool:
    if not messages_equal(left, right):
        return False

    left_notes = [note for note in left.notes if note.kind != NoteKind.INSTANTIATION]
    right_notes = [note for note in right.notes if note.kind != NoteKind.INSTANTIATION]

    return (
        len(left_notes) == len(right_notes)
        and all(
            notes_equal(left_note, right_note)
            for left_note, right_note in zip(
                left_notes,
                right_notes,
                strict=True,
            )
        )
        and [entry.text for entry in left.include_context]
        == [entry.text for entry in right.include_context]
    )


@beartype
def group_diagnostics(
    diagnostics: list[Diagnostic],
) -> list[list[Diagnostic]]:
    groups: list[list[Diagnostic]] = []

    for diagnostic in diagnostics:
        for group in groups:
            if diagnostics_equal(group[0], diagnostic):
                group.append(diagnostic)
                break
        else:
            groups.append([diagnostic])

    return groups


@beartype
def collect_instantiation_stacks(
    diagnostics: list[Diagnostic],
) -> list[list[DiagnosticNote]]:
    stacks: list[list[DiagnosticNote]] = []

    for diagnostic in diagnostics:
        stack = [
            note for note in diagnostic.notes if note.kind == NoteKind.INSTANTIATION
        ]

        if any(
            len(stack) == len(existing)
            and all(
                notes_equal(left, right)
                for left, right in zip(stack, existing, strict=True)
            )
            for existing in stacks
        ):
            continue

        stacks.append(stack)

    return stacks


@beartype
def instantiation_context_equal(
    left: DiagnosticNote,
    right: DiagnosticNote,
) -> bool:
    return (
        left.kind == right.kind
        and left.severity == right.severity
        and location_key(left.location) == location_key(right.location)
        and left.source == right.source
        and [
            (
                part.type,
                part.text if part.type != MessagePartKind.TYPE else "",
            )
            for part in left.message
        ]
        == [
            (
                part.type,
                part.text if part.type != MessagePartKind.TYPE else "",
            )
            for part in right.message
        ]
    )


@beartype
def build_instantiation_tree(
    stacks: list[list[DiagnosticNote]],
    annotation_column: int,
) -> Tree:
    prefix_length = 0

    for index in range(min(len(stack) for stack in stacks)):
        if not all(
            instantiation_context_equal(stacks[0][index], stack[index])
            for stack in stacks[1:]
        ):
            break

        prefix_length += 1

    frames = [[stack[index] for stack in stacks] for index in range(prefix_length)]
    tree = Tree(
        render_instantiation_frames(
            frames,
            annotation_column=annotation_column,
        )
        if frames
        else Text(),
        hide_root=not frames,
        guide_style="dim",
        highlight=False,
    )

    suffix_groups: list[list[list[DiagnosticNote]]] = []
    has_ending = False

    for stack in stacks:
        suffix = stack[prefix_length:]

        if not suffix:
            has_ending = True
            continue

        for group in suffix_groups:
            if instantiation_context_equal(group[0][0], suffix[0]):
                group.append(suffix)
                break
        else:
            suffix_groups.append([suffix])

    if has_ending and suffix_groups:
        tree.add(Text("Stack ends here", style="dim"))

    for group in suffix_groups:
        tree.add(build_instantiation_tree(group, annotation_column))

    return tree


@beartype
def render_instantiation_stacks(
    stacks: list[list[DiagnosticNote]],
) -> Text:
    if len(stacks) == 1:
        return render_instantiation_stack(stacks[0])

    annotation_column = (
        max(
            (
                line.cell_len
                for stack in stacks
                for note in stack
                for line in render_source(note.source).split(
                    "\n",
                    allow_blank=True,
                )
            ),
            default=0,
        )
        + 4
    )
    tree = build_instantiation_tree(stacks, annotation_column)
    label_width = max(
        (
            line.cell_len
            for stack in stacks
            for line in render_instantiation_stack(
                stack,
                annotation_column=annotation_column,
            ).split("\n", allow_blank=True)
        ),
        default=0,
    )
    console = Console(
        file=StringIO(),
        width=max(
            1,
            label_width + 4 * (max(len(stack) for stack in stacks) + 1),
        ),
        markup=False,
        highlight=False,
        soft_wrap=True,
    )
    result = Text(no_wrap=True, overflow="ignore")

    for segment in console.render(tree):
        if not segment.control:
            result.append(segment.text, style=segment.style)

    if result.plain.endswith("\n"):
        return result[:-1]

    return result


@beartype
def render_diagnostic(
    diagnostic: Diagnostic,
    *,
    duplicates: list[Diagnostic] | None = None,
) -> Text:
    result = render_diagnostic_message(diagnostic)
    stacks = collect_instantiation_stacks(
        duplicates if duplicates is not None else [diagnostic]
    )

    group_order = (
        NoteKind.CONTEXT,
        NoteKind.CANDIDATE,
        NoteKind.SUBSTITUTION,
        NoteKind.INSTANTIATION,
        NoteKind.MACRO_EXPANSION,
    )

    for kind in group_order:
        notes = [note for note in diagnostic.notes if note.kind == kind]

        if kind == NoteKind.INSTANTIATION:
            count = max((len(stack) for stack in stacks), default=0)
        else:
            count = len(notes)

        if not count:
            continue

        result.append("\n\n")
        result.append(
            f"{note_group_title(kind)} ({count})",
            style="bold",
        )

        if kind == NoteKind.INSTANTIATION:
            result.append("\n")
            result.append_text(indent_text(render_instantiation_stacks(stacks), 2))
            continue

        for index, note in enumerate(notes):
            compact = kind in (NoteKind.CONTEXT, NoteKind.CANDIDATE)
            result.append("\n" if compact or not index else "\n\n")

            match kind:
                case NoteKind.CANDIDATE:
                    result.append_text(render_candidate(note))

                case NoteKind.CONTEXT:
                    message = render_message_parts(note.message)

                    if note.severity != Severity.NOTE:
                        heading = Text(
                            f"{note.severity.value}: ",
                            style=severity_style(note.severity),
                        )
                        heading.append_text(message)
                        message = heading

                    if note.location is not None:
                        message.append(" ")
                        message.append_text(render_location(note.location))

                    if note.source:
                        message.append("\n")
                        message.append_text(indent_text(render_source(note.source), 2))

                    result.append_text(indent_text(message, 2))

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
    diagnostic_groups: dict[int, list[Diagnostic]] | None = None,
) -> str:
    if (
        not segment.diagnostics
        and not segment.unattached_include_context
        and not segment.summaries
    ):
        return segment.text

    if diagnostic_groups is None:
        diagnostic_groups = {
            id(diagnostic): group
            for group in group_diagnostics(segment.diagnostics)
            for diagnostic in group
        }

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
    rendered_count = 0

    for diagnostic in segment.diagnostics:
        group = diagnostic_groups[id(diagnostic)]

        if group[0] is not diagnostic:
            continue

        if rendered_count:
            console.print()

        console.print(render_diagnostic(diagnostic, duplicates=group))
        rendered_count += 1

    if segment.unattached_include_context:
        if rendered_count:
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
def format_clang_segment_group(
    segment_list: list[ClangSegment],
    *,
    output: StringIO,
    color: bool,
):
    diagnostics = [
        diagnostic
        for segment in segment_list
        if isinstance(segment, ClangSegment)
        for diagnostic in segment.diagnostics
    ]
    diagnostic_groups = {
        id(diagnostic): group
        for group in group_diagnostics(diagnostics)
        for diagnostic in group
    }

    for segment in segment_list:  # noqa
        output.write(
            format_clang_segment(
                segment,
                color=color,
                diagnostic_groups=diagnostic_groups,
            )
        )
