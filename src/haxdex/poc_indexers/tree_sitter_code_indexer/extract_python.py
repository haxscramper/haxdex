from __future__ import annotations

from pathlib import Path

import tree_sitter_python
from loguru import logger
from tree_sitter import Language, Node, Parser

from haxdex.poc_indexers.tree_sitter_code_indexer.code_model import (
    DocInfo,
    ParsedArgument,
    ParsedEntry,
)
from haxdex.poc_indexers.tree_sitter_code_indexer.utils import (
    node_text,
    normalize_doc_lines,
    source_lines,
)

PYTHON_LANGUAGE = Language(tree_sitter_python.language())

PYTHON_SUFFIXES = {
    ".py",
}

_PYTHON_CONTAINER_TYPES = {
    "if_statement",
    "elif_clause",
    "else_clause",
    "try_statement",
    "except_clause",
    "finally_clause",
    "with_statement",
    "for_statement",
    "while_statement",
    "match_statement",
    "case_clause",
    "block",
}

_PYTHON_IGNORED_TYPES = {
    "comment",
    "expression_statement",
    "future_import_statement",
    "import_from_statement",
    "import_statement",
    "print_statement",
}


def clean_python_docstring(raw: str) -> DocInfo | None:
    text = raw.strip()
    if (text.startswith(('"""', "'''")) and text.endswith(('"""', "'''")) and
            len(text) >= 6):
        text = text[3:-3]
    elif text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        text = text[1:-1]

    lines = text.splitlines()
    if not lines:
        return None

    indent_candidates: list[int] = []
    for line in lines[1:]:
        if line.strip():
            indent_candidates.append(len(line) - len(line.lstrip(" \t")))
    common_indent = min(indent_candidates) if indent_candidates else 0

    normalized: list[str] = []
    for i, line in enumerate(lines):
        if i == 0:
            normalized.append(line.strip())
        else:
            normalized.append(line[common_indent:].rstrip())

    return normalize_doc_lines(normalized)


def get_python_docstring_for_statement_list(node: Node, source: bytes) -> DocInfo | None:
    if node.named_child_count == 0:
        return None

    first = node.named_children[0]
    if first.type != "expression_statement" or first.named_child_count != 1:
        return None

    expr = first.named_children[0]
    if expr.type not in {"string", "concatenated_string"}:
        return None

    return clean_python_docstring(node_text(expr, source))


def python_unwrap_decorated_definition(node: Node) -> Node | None:
    if node.type != "decorated_definition":
        return node

    return node.child_by_field_name("definition")


def python_parse_module_scope(
    node: Node,
    source: bytes,
    output: list[ParsedEntry],
    qualified_prefix: list[str],
    rel_path: str,
) -> None:
    for child in node.named_children:
        definition = python_unwrap_decorated_definition(child)
        if definition is None:
            continue

        if definition.type in {
                "function_definition",
                "async_function_definition",
        }:
            fn = python_parse_function(
                definition,
                source,
                "function",
                qualified_prefix,
                rel_path=rel_path,
            )
            if fn is not None:
                output.append(fn)
            continue

        if definition.type == "class_definition":
            cls = python_parse_class(
                definition,
                source,
                qualified_prefix,
                rel_path=rel_path,
            )
            if cls is not None:
                output.append(cls)
            continue

        if child.type in _PYTHON_CONTAINER_TYPES:
            python_parse_module_scope(
                child,
                source,
                output,
                qualified_prefix,
                rel_path,
            )
            continue

        if child.type in _PYTHON_IGNORED_TYPES:
            continue

        if child.type not in _SEEN_TYPES_PY:
            logger.warning(f"unexpected type {child.type}")
            logger.warning(f"{child}")
            _SEEN_TYPES_PY.add(child.type)


def python_extract_function_name(node: Node, source: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return node_text(name_node, source)


def python_parse_arguments(params_node: Node | None,
                           source: bytes) -> list[ParsedArgument]:
    if params_node is None:
        return []

    args: list[ParsedArgument] = []
    pos = 0
    for child in params_node.named_children:
        arg = ParsedArgument(name=None)

        if child.type == "identifier":
            arg.name = node_text(child, source)
            arg.kind = "positional"
        elif child.type == "typed_parameter":
            n = child.child_by_field_name("name")
            t = child.child_by_field_name("type")
            arg.name = node_text(n, source) if n is not None else None
            arg.type_text = node_text(t, source) if t is not None else None
            arg.kind = "positional"
        elif child.type == "default_parameter":
            n = child.child_by_field_name("name")
            v = child.child_by_field_name("value")
            arg.name = node_text(n, source) if n is not None else None
            arg.default_value = node_text(v, source) if v is not None else None
            arg.kind = "positional"
        elif child.type == "typed_default_parameter":
            n = child.child_by_field_name("name")
            t = child.child_by_field_name("type")
            v = child.child_by_field_name("value")
            arg.name = node_text(n, source) if n is not None else None
            arg.type_text = node_text(t, source) if t is not None else None
            arg.default_value = node_text(v, source) if v is not None else None
            arg.kind = "positional"
        elif child.type == "list_splat_pattern":
            arg.name = node_text(child, source).lstrip("*")
            arg.kind = "vararg"
        elif child.type == "dictionary_splat_pattern":
            arg.name = node_text(child, source).lstrip("*")
            arg.kind = "kwvararg"
        else:
            continue

        if arg.name is not None:
            args.append(arg)
            pos += 1

    return args


def python_parse_function(
    node: Node,
    source: bytes,
    kind: str,
    qualified_prefix: list[str],
    rel_path: str,
) -> ParsedEntry | None:
    name = python_extract_function_name(node, source)
    if name is None:
        return None

    body = node.child_by_field_name("body")
    params = node.child_by_field_name("parameters")
    return_type = node.child_by_field_name("return_type")
    doc = (get_python_docstring_for_statement_list(body, source)
           if body is not None else None)
    args = python_parse_arguments(params, source)

    params_text = node_text(params, source) if params is not None else "()"
    return_text = (f" -> {node_text(return_type, source)}"
                   if return_type is not None else "")
    signature = f"{params_text}{return_text}"

    qn = ".".join(qualified_prefix + [name])
    start_line, end_line = source_lines(node)

    return ParsedEntry(
        kind=kind,
        name=name,
        language="python",
        qualified_name=qn,
        start_line=start_line,
        end_line=end_line,
        doc=doc,
        type_text=node_text(return_type, source) if return_type is not None else None,
        signature_text=signature,
        signature_source=f"{name}{signature}",
        arguments=args,
        path=rel_path,
    )


def python_parse_class(
    node: Node,
    source: bytes,
    qualified_prefix: list[str],
    rel_path: str,
) -> ParsedEntry | None:
    name = python_extract_function_name(node, source)
    if name is None:
        return None

    body = node.child_by_field_name("body")
    doc = (get_python_docstring_for_statement_list(body, source)
           if body is not None else None)
    start_line, end_line = source_lines(node)
    qn = ".".join(qualified_prefix + [name])

    cls = ParsedEntry(
        kind="class",
        name=name,
        language="python",
        qualified_name=qn,
        start_line=start_line,
        end_line=end_line,
        doc=doc,
        path=rel_path,
    )

    if body is None:
        return cls

    for child in body.named_children:
        definition = python_unwrap_decorated_definition(child)
        if definition is None:
            continue

        if definition.type in {
                "function_definition",
                "async_function_definition",
        }:
            method = python_parse_function(
                definition,
                source,
                "method",
                qualified_prefix + [name],
                rel_path=rel_path,
            )
            if method is not None:
                cls.children.append(method)
            continue

        statement = child
        if child.type == "expression_statement":
            if len(child.named_children) != 1:
                continue
            statement = child.named_children[0]

        if statement.type not in {"assignment", "typed_assignment"}:
            continue

        left = statement.child_by_field_name("left")
        tnode = statement.child_by_field_name("type")
        type_text = node_text(tnode, source) if tnode is not None else None
        if left is None:
            continue

        targets: list[str] = []
        if left.type == "identifier":
            targets.append(node_text(left, source))
        elif left.type in {"pattern_list", "tuple_pattern"}:
            for sub in left.named_children:
                if sub.type == "identifier":
                    targets.append(node_text(sub, source))

        fstart, fend = source_lines(statement)
        for target in targets:
            cls.children.append(
                ParsedEntry(
                    kind="field",
                    name=target,
                    language="python",
                    qualified_name=".".join(qualified_prefix + [name, target]),
                    start_line=fstart,
                    end_line=fend,
                    doc=None,
                    type_text=type_text,
                    path=rel_path,
                ))

    return cls


_SEEN_TYPES_PY = set()


def index_python_file(path: Path, rel_path: str, parser: Parser) -> ParsedEntry:
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
        doc=get_python_docstring_for_statement_list(root, source)
        if root.type == "module" else None,
    )

    python_parse_module_scope(
        root,
        source,
        file_entry.children,
        [],
        rel_path,
    )

    return file_entry
