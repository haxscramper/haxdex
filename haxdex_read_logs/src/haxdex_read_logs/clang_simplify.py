import json
from pathlib import Path

from beartype import beartype
from haxdex_read_logs.clang_error_model import ClangSegment, CompileLog, MessagePartKind


@beartype
def simplify_types(types: list[str]) -> list[str]:
    noquote = [it.strip("'") for it in types]
    Path("/tmp/type_segments.json").write_text(json.dumps(noquote, indent=2))
    return types


@beartype
def rewrite_compile_log_types(compile_log: CompileLog) -> CompileLog:
    result = compile_log.model_copy(deep=True)

    type_parts = [
        part
        for segment in result.segments
        if isinstance(segment, ClangSegment)
        for diagnostic in segment.diagnostics
        for message in (diagnostic, *diagnostic.notes)
        for part in message.message
        if part.type == MessagePartKind.TYPE
    ]

    simplified = simplify_types([part.text for part in type_parts])

    for part, text in zip(type_parts, simplified, strict=True):
        part.text = text

    return result
