from __future__ import annotations

import re

from tree_sitter import Language, Node, Parser

from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.code_model import DocInfo


def make_parser(language: Language) -> Parser:
    parser = Parser()
    parser.language = language
    return parser


def node_text(node: Node, source: bytes, normalize: bool = True) -> str:
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    if normalize:
        text = re.sub(r"\r\n?|\n", "", text)
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r" {2,}", " ", text)
        assert "\n" not in text
    return text


def source_lines(node: Node) -> tuple[int | None, int | None]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def format_eta(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def normalize_doc_lines(lines: list[str]) -> DocInfo | None:
    cleaned = [line.rstrip() for line in lines]
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    if not cleaned:
        return None

    full = "\n".join(cleaned).strip()
    if not full:
        return None

    brief = None
    for line in cleaned:
        if line.strip():
            brief = line.strip()
            brief = brief.replace("\\brief", "")
            break

    return DocInfo(brief=brief, full=full)


def find_first_descendant(node: Node, target_types: set[str]) -> Node | None:
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in target_types:
            return cur
        for child in reversed(cur.children):
            stack.append(child)
    return None
