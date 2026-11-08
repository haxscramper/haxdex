from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import plumbum
from beartype import beartype
from beartype.typing import Annotated, Literal, Optional
from haxdex_read_logs.clang_error_model import ClangSegment, CompileLog, MessagePartKind
from haxdex_read_logs.cppdecl_type_model import (
    FrozenModel,
    NormalizedRoot,
    QualifiedName,
    QualType,
    to_qual_type,
)
from haxdex_read_logs.qual_type_rewrite import QualTypeRewriter
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


type QualOrStr = QualType | str


def configure_rewrite(r: QualTypeRewriter):
    from haxdex_read_logs.qual_type_rewrite import T

    # std::vector<Arg> -> vector<Arg>
    r.rule(
        (["std"], "vector", [T.bind("Arg")]),
        ([], "vector", [T.get("Arg")]),
    )

    # std::variant<...> -> std::variant<>
    r.rule(
        (["std"], "variant", ...),
        (["std"], "variant", []),
    )

    # std::tuple<Args...> -> Tuple<Args...>
    r.rule(
        (["std"], "tuple", [T.bind("Args", ...)]),
        ([], "Tuple", [T.get("Args")]),
    )

    # First<Arg, ...> -> Arg
    r.rule(
        ([], "First", [T.bind("Arg"), ...]),
        T.get("Arg"),
    )

    # hstd::unidata_dsl::test::Derived -> test::Derived
    r.rule(
        (["hstd", "unidata_dsl", "test"], T.bind("Type"), T.bind("Args", ...)),
        (["test"], T.get("Type"), T.get("Args")),
    )

    r.rule(
        (["hstd", "ext", "unidata"], T.bind("Type"), T.bind("Args", ...)),
        (["ud"], T.get("Type"), T.get("Args")),
    )

    r.rule(
        (..., ["UniValue"], [(..., "StaticT", [T.bind("Arg")])]),
        ([], "UniStatic", [T.get("Arg")]),
    )


@beartype
def simplify_types(types: list[str]) -> list[str]:
    parsed = parse_types(types)

    def map_qual_type[T](
        items: list[QualOrStr], mapped: Callable[[QualType], T]
    ) -> list[T | str]:
        return [mapped(t) if isinstance(t, QualType) else t for t in items]

    qualified_list = [
        to_qual_type(t.root.normalized) if t.root.normalized else t.root.original
        for t in parsed
    ]

    Path("/tmp/intermediate.json").write_text(
        json.dumps(
            map_qual_type(
                qualified_list, lambda it: it.model_dump(exclude_defaults=True)
            ),
            indent=2,
        )
    )

    rewriter = QualTypeRewriter()
    configure_rewrite(rewriter)

    simplified_list = map_qual_type(qualified_list, lambda t: rewriter.rewrite(t))

    Path("/tmp/simplified.json").write_text(
        json.dumps(
            map_qual_type(
                simplified_list, lambda it: it.model_dump(exclude_defaults=True)
            ),
            indent=2,
        )
    )

    result = map_qual_type(simplified_list, lambda it: it.format())

    Path("/tmp/compare.json").write_text(
        json.dumps(
            [dict(before=t1, after=t2) for t1, t2 in zip(types, result)], indent=2
        )
    )

    return result


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
