from __future__ import annotations

from pathlib import Path

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.code_model import (
    DocInfo,
    ParsedArgument,
    ParsedEntry,
)
from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.utils import (
    find_first_descendant,
    node_text,
    normalize_doc_lines,
    source_lines,
)

CPP_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
}

CPP_LANGUAGE = Language(tree_sitter_cpp.language())


def clean_cpp_comment_text(raw: str) -> list[str]:
    lines = raw.splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            stripped = stripped[2:]
            stripped = stripped.removeprefix("/")
            result.append(stripped.lstrip())
            continue

        stripped = stripped.removeprefix("/*")
        stripped = stripped.removesuffix("*/")
        stripped = stripped.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].lstrip()
        result.append(stripped)

    return result


def extract_leading_cpp_doc(parent: Node, target_index: int,
                            source: bytes) -> DocInfo | None:
    lines: list[str] = []
    idx = target_index - 1
    saw_comment = False

    while idx >= 0:
        node = parent.named_children[idx]
        if node.type == "comment":
            lines = clean_cpp_comment_text(node_text(node, source)) + lines
            saw_comment = True
            idx -= 1
            continue

        if saw_comment:
            gap = parent.named_children[idx + 1].start_point[0] - node.end_point[0]
            if gap <= 1:
                idx -= 1
                continue
        break

    return normalize_doc_lines(lines)


def cpp_extract_declarator_name(node: Node, source: bytes) -> str | None:
    if node.type in {"identifier", "field_identifier", "type_identifier"}:
        return node_text(node, source)

    for child in node.children:
        result = cpp_extract_declarator_name(child, source)
        if result is not None:
            return result
    return None


def cpp_parse_arguments_from_declarator(
        declarator: Node, source: bytes) -> tuple[list[ParsedArgument], str | None]:
    parameter_list = find_first_descendant(declarator, {"parameter_list"})
    if parameter_list is None:
        return [], None

    args: list[ParsedArgument] = []
    for child in parameter_list.named_children:
        if child.type not in {
                "parameter_declaration",
                "optional_parameter_declaration",
        }:
            continue

        type_node = child.child_by_field_name("type")
        declarator_node = child.child_by_field_name("declarator")
        default_node = child.child_by_field_name("default_value")

        arg_name = (cpp_extract_declarator_name(declarator_node, source)
                    if declarator_node is not None else None)
        arg_type = (node_text(type_node, source).strip()
                    if type_node is not None else None)
        arg_default = (node_text(default_node, source).strip()
                       if default_node is not None else None)

        args.append(
            ParsedArgument(
                name=arg_name,
                type_text=arg_type,
                default_value=arg_default,
                kind=None,
            ))

    declarator_text = node_text(declarator, source)
    params_text = node_text(parameter_list, source)
    suffix = params_text
    idx = declarator_text.find(params_text)
    if idx != -1:
        trailing = declarator_text[idx + len(params_text):].strip()
        if trailing:
            suffix = f"{params_text} {trailing}"

    return args, suffix


def cpp_parse_function_entry(
    node: Node,
    source: bytes,
    kind: str,
    qualified_prefix: list[str],
    doc: DocInfo | None,
    rel_path: str,
) -> ParsedEntry | None:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return None

    name = cpp_extract_declarator_name(declarator, source)
    if name is None:
        return None

    type_node = node.child_by_field_name("type")
    return_type = (node_text(type_node, source).strip()
                   if type_node is not None else None)
    args, signature_suffix = cpp_parse_arguments_from_declarator(declarator, source)
    start_line, end_line = source_lines(node)
    qn = "::".join(qualified_prefix + [name])

    return ParsedEntry(
        kind=kind,
        name=name,
        language="cpp",
        qualified_name=qn,
        start_line=start_line,
        end_line=end_line,
        doc=doc,
        type_text=return_type,
        signature_text=signature_suffix,
        signature_source=node_text(declarator, source).strip(),
        arguments=args,
        path=rel_path,
    )


def cpp_collect_field_names(node: Node, source: bytes) -> list[str]:
    names: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in {"field_identifier", "identifier"}:
            names.append(node_text(cur, source))
        for child in reversed(cur.children):
            stack.append(child)
    unique = list(dict.fromkeys(names))
    return unique


def cpp_parse_class(
    node: Node,
    source: bytes,
    qualified_prefix: list[str],
    doc: DocInfo | None,
    rel_path: str,
) -> ParsedEntry | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None

    class_name = node_text(name_node, source).strip()
    start_line, end_line = source_lines(node)
    qn = "::".join(qualified_prefix + [class_name])

    entry = ParsedEntry(
        kind="class",
        name=class_name,
        language="cpp",
        qualified_name=qn,
        start_line=start_line,
        end_line=end_line,
        doc=doc,
        path=rel_path,
    )

    body = node.child_by_field_name("body")
    if body is None:
        return entry

    signal_section = False
    for idx, child in enumerate(body.named_children):
        child_doc = extract_leading_cpp_doc(body, idx, source)

        if child.type == "access_specifier":
            access_text = node_text(child, source).strip().rstrip(":")
            signal_section = access_text == "signals" or "signals" in access_text
            continue

        if child.type == "function_definition":
            method = cpp_parse_function_entry(
                child,
                source,
                "signal" if signal_section else "method",
                qualified_prefix + [class_name],
                child_doc,
                rel_path=rel_path,
            )
            if method is not None:
                entry.children.append(method)
            continue

        if child.type == "declaration":
            declarator = child.child_by_field_name("declarator")
            if declarator is not None:
                has_params = (find_first_descendant(declarator, {"parameter_list"})
                              is not None)
                if has_params:
                    method = cpp_parse_function_entry(
                        child,
                        source,
                        "signal" if signal_section else "method",
                        qualified_prefix + [class_name],
                        child_doc,
                        rel_path=rel_path,
                    )
                    if method is not None:
                        entry.children.append(method)
                    continue

            type_node = child.child_by_field_name("type")
            field_type = (node_text(type_node, source).strip()
                          if type_node is not None else None)
            field_names = cpp_collect_field_names(child, source)
            for fname in field_names:
                fstart, fend = source_lines(child)
                entry.children.append(
                    ParsedEntry(
                        kind="field",
                        name=fname,
                        language="cpp",
                        qualified_name="::".join(qualified_prefix + [class_name, fname]),
                        start_line=fstart,
                        end_line=fend,
                        doc=child_doc,
                        type_text=field_type,
                        path=rel_path,
                    ))
            continue

        if child.type == "field_declaration":
            type_node = child.child_by_field_name("type")
            field_type = (node_text(type_node, source).strip()
                          if type_node is not None else None)
            field_names = cpp_collect_field_names(child, source)
            for fname in field_names:
                fstart, fend = source_lines(child)
                entry.children.append(
                    ParsedEntry(
                        kind="field",
                        name=fname,
                        language="cpp",
                        qualified_name="::".join(qualified_prefix + [class_name, fname]),
                        start_line=fstart,
                        end_line=fend,
                        doc=child_doc,
                        type_text=field_type,
                        path=rel_path,
                    ))

    return entry


def cpp_parse_enum(
    node: Node,
    source: bytes,
    qualified_prefix: list[str],
    doc: DocInfo | None,
    rel_path: str,
) -> ParsedEntry | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None

    enum_name = node_text(name_node, source).strip()
    start_line, end_line = source_lines(node)
    base_node = node.child_by_field_name("base")

    entry = ParsedEntry(
        kind="enum",
        name=enum_name,
        language="cpp",
        qualified_name="::".join(qualified_prefix + [enum_name]),
        start_line=start_line,
        end_line=end_line,
        doc=doc,
        type_text=node_text(base_node, source).strip() if base_node is not None else None,
        path=rel_path,
    )

    body = node.child_by_field_name("body")
    if body is None:
        return entry

    for eidx, child in enumerate(body.named_children):
        if child.type != "enumerator":
            continue
        ename_node = child.child_by_field_name("name")
        if ename_node is None:
            continue
        ename = node_text(ename_node, source).strip()
        value_node = child.child_by_field_name("value")
        estart, eend = source_lines(child)
        entry.children.append(
            ParsedEntry(
                kind="enum_constant",
                name=ename,
                language="cpp",
                qualified_name="::".join(qualified_prefix + [enum_name, ename]),
                start_line=estart,
                end_line=eend,
                doc=extract_leading_cpp_doc(body, eidx, source),
                type_text=node_text(value_node, source).strip()
                if value_node is not None else None,
                path=rel_path,
            ))

    return entry


_SEEN_TYPES = set()

_CPP_PREPROC_BRANCH_TYPES = {
    "preproc_if",
    "preproc_ifdef",
    "preproc_else",
    "preproc_elif",
}

_CPP_SCOPE_JUNK_TYPES = {
    "comment",
    "preproc_def",
    "preproc_call",
    "preproc_include",
    "preproc_function_def",
    "expression_statement",
    "using_declaration",
    "namespace_alias_definition",
    "static_assert_declaration",
    "template_instantiation",
    "labeled_statement",
    "return_statement",
    "for_statement",
    "if_statement",
    "compound_statement",
    "identifier",
    "unary_expression",
    "binary_expression",
    "ERROR",
}


def cpp_parse_scope(
    node: Node,
    source: bytes,
    out: list[ParsedEntry],
    qualified_prefix: list[str],
    rel_path: str,
) -> None:
    for idx, child in enumerate(node.named_children):
        child_doc = extract_leading_cpp_doc(node, idx, source)
        cpp_parse_scope_child(child, child_doc, source, out, qualified_prefix, rel_path)


def cpp_parse_scope_child(
    child: Node,
    child_doc: DocInfo | None,
    source: bytes,
    out: list[ParsedEntry],
    qualified_prefix: list[str],
    rel_path: str,
) -> None:
    match child.type:
        case branch if branch in _CPP_PREPROC_BRANCH_TYPES:
            cpp_parse_scope(child, source, out, qualified_prefix, rel_path=rel_path)

        case "template_declaration":
            for sub in child.named_children:
                if sub.type in {
                        "template_parameter_list",
                        "requires_clause",
                        "comment",
                }:
                    continue
                cpp_parse_scope_child(sub, child_doc, source, out, qualified_prefix,
                                      rel_path)

        case "linkage_specification":
            body = child.child_by_field_name("body")
            if body is None:
                return
            if body.type == "declaration_list":
                cpp_parse_scope(body, source, out, qualified_prefix, rel_path=rel_path)
            else:
                cpp_parse_scope_child(body, child_doc, source, out, qualified_prefix,
                                      rel_path)

        case "namespace_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            ns_name = node_text(name_node, source).strip()
            ns_qn = "::".join(qualified_prefix + [ns_name])
            ns_start, ns_end = source_lines(child)

            ns_entry = ParsedEntry(
                kind="namespace",
                name=ns_name,
                language="cpp",
                qualified_name=ns_qn,
                start_line=ns_start,
                end_line=ns_end,
                doc=child_doc,
                path=rel_path,
            )

            body = child.child_by_field_name("body")
            if body is not None:
                cpp_parse_scope(
                    body,
                    source,
                    ns_entry.children,
                    qualified_prefix + [ns_name],
                    rel_path=rel_path,
                )

            out.append(ns_entry)

        case "class_specifier" | "struct_specifier":
            cls = cpp_parse_class(
                child,
                source,
                qualified_prefix,
                child_doc,
                rel_path=rel_path,
            )
            if cls is not None:
                out.append(cls)

        case "enum_specifier":
            en = cpp_parse_enum(
                child,
                source,
                qualified_prefix,
                child_doc,
                rel_path=rel_path,
            )
            if en is not None:
                out.append(en)

        case "alias_declaration":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            alias_name = node_text(name_node, source).strip()
            type_node = child.child_by_field_name("type")
            start_line, end_line = source_lines(child)
            out.append(
                ParsedEntry(
                    kind="type_alias",
                    name=alias_name,
                    language="cpp",
                    qualified_name="::".join(qualified_prefix + [alias_name]),
                    start_line=start_line,
                    end_line=end_line,
                    doc=child_doc,
                    type_text=node_text(type_node, source).strip()
                    if type_node is not None else None,
                    signature_source=node_text(child, source).strip(),
                    path=rel_path,
                ))

        case "type_definition":
            type_node = child.child_by_field_name("type")
            type_text = (node_text(type_node, source).strip()
                         if type_node is not None else None)
            start_line, end_line = source_lines(child)
            for decl in child.children_by_field_name("declarator"):
                alias_name = cpp_extract_declarator_name(decl, source)
                if alias_name is None:
                    continue
                out.append(
                    ParsedEntry(
                        kind="type_alias",
                        name=alias_name,
                        language="cpp",
                        qualified_name="::".join(qualified_prefix + [alias_name]),
                        start_line=start_line,
                        end_line=end_line,
                        doc=child_doc,
                        type_text=type_text,
                        signature_source=node_text(child, source).strip(),
                        path=rel_path,
                    ))

        case "function_definition":
            fn = cpp_parse_function_entry(
                child,
                source,
                "function",
                qualified_prefix,
                child_doc,
                rel_path=rel_path,
            )
            if fn is not None:
                out.append(fn)

        case "declaration":
            declarator = child.child_by_field_name("declarator")
            if declarator is None:
                return
            has_params = (find_first_descendant(declarator, {"parameter_list"})
                          is not None)
            if not has_params:
                return
            fn = cpp_parse_function_entry(
                child,
                source,
                "function",
                qualified_prefix,
                child_doc,
                rel_path=rel_path,
            )
            if fn is not None:
                out.append(fn)

        case junk if junk in _CPP_SCOPE_JUNK_TYPES:
            pass

        case _:
            if child.type not in _SEEN_TYPES:
                # logger.warning(f"unexpected type {child.type}")
                # logger.warning(f"{child}")
                _SEEN_TYPES.add(child.type)


def index_cpp_file(path: Path, rel_path: str, parser: Parser) -> ParsedEntry:
    source = path.read_bytes()
    tree = parser.parse(source)
    root = tree.root_node
    start_line, end_line = source_lines(root)

    file_entry = ParsedEntry(
        kind="file",
        name=path.name,
        language=None,
        path=rel_path,
        qualified_name=rel_path,
        start_line=start_line,
        end_line=end_line,
    )

    cpp_parse_scope(root, source, file_entry.children, [], rel_path=rel_path)
    return file_entry
