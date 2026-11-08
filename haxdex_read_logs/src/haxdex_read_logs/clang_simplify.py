from __future__ import annotations

import json
from pathlib import Path

import plumbum
from beartype import beartype
from beartype.typing import Annotated, Literal, Optional
from haxdex_read_logs.clang_error_model import ClangSegment, CompileLog, MessagePartKind
from haxdex_read_logs.cppdecl_type_model import (
    FrozenModel,
    NormalizedName,
    NormalizedRoot,
    QualifiedName,
)
from pydantic import ConfigDict, Field, RootModel


class QualifiedNameParseSuccess(FrozenModel):
    ok: Literal[True]
    normalized: NormalizedRoot
    parsed: Optional[QualifiedName]
    original: str


class QualifiedNameParseFailure(FrozenModel):
    ok: Literal[False]
    normalized: None
    parsed: None
    original: str


class QualifiedNameParseResult(
    RootModel[
        Annotated[
            QualifiedNameParseSuccess | QualifiedNameParseFailure,
            Field(discriminator="ok"),
        ]
    ]
):
    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=False,
        strict=True,
    )


@beartype
class QualType(FrozenModel):
    name: str
    params: list[QualType] = Field(default_factory=list)
    scopes: list[QualType] = Field(default_factory=list)
    qualifiers: list[str] = Field(default_factory=list)


@beartype
def to_qual_type(value: NormalizedRoot | NormalizedName) -> QualType:
    match value:
        case NormalizedRoot():
            return to_qual_type(value.names)

        case NormalizedName():
            qualifiers = [qualifier.t.value for qualifier in value.name.cvr]
            params = [to_qual_type(param) for param in value.params]

            if value.name.name == "<qualified>":
                if not params:
                    raise ValueError(
                        "Cannot simplify '<qualified>': params is empty; "
                        "expected scope components followed by a terminal name."
                    )

                terminal = params[-1]
                return terminal.model_copy(
                    update=dict(
                        scopes=[*params[:-1], *terminal.scopes],
                        qualifiers=[*qualifiers, *terminal.qualifiers],
                    )
                )

            return QualType(
                name=value.name.name,
                params=params,
                qualifiers=qualifiers,
            )


@beartype
def parse_types(types: list[str]) -> list[QualifiedNameParseResult]:
    input_seg = Path("/tmp/type_segments.json")
    res_seg = Path("/tmp/type_parsed.json")
    input_seg.write_text(json.dumps(types, indent=2))
    tool = "/home/haxscramper/defaultdirs/workspace/repos/haxdex/build/conan_super/build/Debug/haxdex_cpp_build_stats/type_parsing_cli"
    cmd = plumbum.local[tool]
    cmd.run([str(input_seg), str(res_seg)])
    result = [
        QualifiedNameParseResult.model_validate(it)
        for it in json.loads(res_seg.read_text())
    ]

    return result


@beartype
def simplify_types(types: list[str]) -> list[str]:
    noquote = [it.strip("'") for it in types]
    parsed = parse_types(noquote)

    qualified_list = [
        to_qual_type(t.root.normalized) if t.root.normalized else t.root.original
        for t in parsed
    ]

    Path("/tmp/intermediate.json").write_text(
        json.dumps(
            [
                t.model_dump(
                    exclude_defaults=True,
                )
                if isinstance(t, QualType)
                else t
                for t in qualified_list
            ],
            indent=2,
        )
    )

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
