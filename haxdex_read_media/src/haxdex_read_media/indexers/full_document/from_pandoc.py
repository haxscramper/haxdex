import json
from pathlib import Path

import plumbum
from beartype.typing import Sequence

from haxdex_read_media.indexers.full_document.full_document_types import (
    BulletListItem,
    Code,
    CodeBlockProps,
    Div,
    DivProps,
    Document,
    DocumentBlock,
    Heading,
    HeadingProps,
    InlineContent,
    InlineNode,
    Link,
    Markup,
    NumberedListItem,
    Paragraph,
    Quote,
    RawBlock,
    StyledText,
    Table,
    TableCell,
    TableCellProps,
    TableProps,
    TableRow,
    TextAlignment,
    TextStyles,
    build,
    inline_nodes_to_content,
    merge_text,
)


def _convert_inlines(
    inlines: list,
    styles: TextStyles | None = None,
) -> list[InlineNode]:
    styles = styles or TextStyles()
    out: list[InlineNode] = []

    for il in inlines:
        match il:
            case {"t": "Str"}:
                out.append(StyledText(text=il["c"], styles=styles))

            case {"t": "Space"}:
                out.append(StyledText(text=" ", styles=styles))

            case {"t": "SoftBreak" | "LineBreak"}:
                out.append(StyledText(text="\n", styles=styles))

            case {"t": "Emph"}:
                out += _convert_inlines(il["c"], styles.extend(Markup.ITALIC))

            case {"t": "Strong"}:
                out += _convert_inlines(il["c"], styles.extend(Markup.BOLD))

            case {"t": "Strikeout"}:
                out += _convert_inlines(il["c"], styles.extend(Markup.STRIKE))

            case {"t": "Underline"}:
                out += _convert_inlines(il["c"], styles.extend(Markup.UNDERLINE))

            case {"t": "Code"}:
                out.append(
                    StyledText(
                        text=il["c"][1],
                        styles=styles.extend(Markup.CODE),
                    )
                )

            case {"t": "Link" | "Image"}:
                _, link_inlines, target = il["c"]
                label_nodes = _convert_inlines(link_inlines)
                label = [node for node in label_nodes if isinstance(node, StyledText)]
                out.append(
                    Link(
                        href=target[0],
                        content=label,
                    )
                )

            case {"t": "Superscript" | "Subscript" | "SmallCaps"}:
                out += _convert_inlines(il["c"], styles)

            case {"t": "Span" | "Quoted"}:
                out += _convert_inlines(il["c"][-1], styles)

            case {"t": "Math"}:
                out.append(
                    StyledText(
                        text=il["c"][1],
                        styles=styles.extend(Markup.MATH),
                    )
                )

            case {"t": "RawInline"}:
                out.append(
                    StyledText(
                        text="".join(il["c"]),
                        styles=styles.extend(Markup.RAW),
                    )
                )

            case str():
                out.append(StyledText(text=il))

            case _:
                raise RuntimeError(f"Unhandled inline element: {il}")

    return merge_text(out)


def _inline_text(inlines: list[InlineNode]) -> str:
    parts: list[str] = []

    for item in inlines:
        if isinstance(item, StyledText):
            parts.append(item.text)
        elif isinstance(item, Link):
            parts.append("".join(segment.text for segment in item.content))

    return "".join(parts)


def _extract_caption_text(blocks: list[dict]) -> str | None:
    lines: list[str] = []

    for block in blocks:
        if block["t"] in {"Plain", "Para"}:
            text = _inline_text(_convert_inlines(block["c"])).strip()
            if text:
                lines.append(text)

    if not lines:
        return None

    return "\n".join(lines)


def _attach_caption(block: DocumentBlock, caption: str) -> None:
    props = getattr(block, "props", None)

    if props is None:
        return

    if "caption" not in props.__class__.model_fields:
        return

    props.caption = caption


def _convert_list_items(
    items: list,
    item_type: str,
) -> Sequence[DocumentBlock]:
    cls = {
        "bulletListItem": BulletListItem,
        "numberedListItem": NumberedListItem,
    }[item_type]

    result: list[DocumentBlock] = []

    for item in items:
        blocks: list[DocumentBlock] = []

        for pb in item:
            blocks.extend(_convert_block(pb))

        if not blocks:
            continue

        head = blocks[0]
        content = getattr(head, "content", InlineContent())

        result.append(
            build(
                cls,
                content=content,
                nested=blocks[1:],
            )
        )

    return result


def _pandoc_alignment_to_text_alignment(
    alignment: object,
) -> TextAlignment:
    tag = alignment.get("t") if isinstance(alignment, dict) else str(alignment)

    match tag:
        case "AlignCenter":
            return "center"
        case "AlignRight":
            return "right"
        case _:
            return "left"


def _blocks_to_inline_content(blocks: list[dict]) -> InlineContent:
    nodes: list[InlineNode] = []

    for block in blocks:
        match block.get("t"):
            case "Plain" | "Para":
                chunk = _convert_inlines(block["c"])

            case "CodeBlock":
                chunk = [
                    StyledText(
                        text=block["c"][1],
                        styles=TextStyles(markup={Markup.CODE}),
                    )
                ]

            case _:
                chunk = []

        if chunk:
            if nodes:
                nodes.append(StyledText(text="\n"))
            nodes.extend(chunk)

    return inline_nodes_to_content(merge_text(nodes))


def _extract_table_caption(caption_obj: object) -> str | None:
    if isinstance(caption_obj, list):
        if caption_obj and isinstance(caption_obj[0], dict) and "t" in caption_obj[0]:
            return _extract_caption_text(caption_obj)

        for part in caption_obj:
            text = _extract_table_caption(part)
            if text:
                return text

    if isinstance(caption_obj, dict):
        for key in ("long", "blocks", "c"):
            if key in caption_obj:
                text = _extract_table_caption(caption_obj[key])
                if text:
                    return text

    return None


def _row_to_table_row(
    row_obj: list,
    *,
    is_header: bool,
) -> TableRow:
    _row_attr, cells_obj = row_obj
    cells: list[TableCell] = []

    for cell_obj in cells_obj:
        _cell_attr, alignment, row_span, col_span, blocks = cell_obj

        cells.append(
            build(
                TableCell,
                props=TableCellProps(
                    textAlignment=_pandoc_alignment_to_text_alignment(alignment),
                    rowSpan=max(1, int(row_span)),
                    colSpan=max(1, int(col_span)),
                    isHeader=is_header,
                ),
                content=_blocks_to_inline_content(blocks),
            )
        )

    return build(
        TableRow,
        nested=cells,
    )


def _convert_table(pb: dict) -> Table:
    _attr, caption, _colspecs, thead, tbodies, tfoot = pb["c"]
    rows: list[TableRow] = []

    if isinstance(thead, list) and len(thead) == 2:
        for row in thead[1]:
            rows.append(
                _row_to_table_row(
                    row,
                    is_header=True,
                )
            )

    for tbody in tbodies:
        if not isinstance(tbody, list) or len(tbody) != 4:
            continue

        for row in tbody[2]:
            rows.append(
                _row_to_table_row(
                    row,
                    is_header=True,
                )
            )

        for row in tbody[3]:
            rows.append(
                _row_to_table_row(
                    row,
                    is_header=False,
                )
            )

    if isinstance(tfoot, list) and len(tfoot) == 2:
        for row in tfoot[1]:
            rows.append(
                _row_to_table_row(
                    row,
                    is_header=False,
                )
            )

    return build(
        Table,
        props=TableProps(caption=_extract_table_caption(caption)),
        nested=rows,
    )


def _convert_block(pb: dict) -> Sequence[DocumentBlock]:
    t = pb["t"]

    match t:
        case "Para" | "Plain":
            return [
                build(
                    Paragraph,
                    content=inline_nodes_to_content(
                        _convert_inlines(pb["c"], TextStyles())
                    ),
                )
            ]

        case "Header":
            level, attr, inlines = pb["c"]
            props = HeadingProps(level=level)
            props.extra["subtree_auto_id"] = attr[0]

            for entry in attr[2]:
                props.extra[entry[0]] = entry[1]

            return [
                build(
                    Heading,
                    props=props,
                    content=inline_nodes_to_content(
                        _convert_inlines(inlines, TextStyles())
                    ),
                )
            ]

        case "CodeBlock":
            attr, code = pb["c"]
            classes = attr[1]
            lang = classes[0] if classes else "text"

            return [
                build(
                    Code,
                    props=CodeBlockProps(language=lang),
                    content=code,
                )
            ]

        case "BulletList":
            return _convert_list_items(
                pb["c"],
                "bulletListItem",
            )

        case "OrderedList":
            return _convert_list_items(
                pb["c"][1],
                "numberedListItem",
            )

        case "DefinitionList":
            blocks: list[DocumentBlock] = []

            for term_inlines, definitions in pb["c"]:
                term = _convert_inlines(term_inlines)

                for def_blocks in definitions:
                    definition_inlines: list[InlineNode] = []

                    for db in def_blocks:
                        match db:
                            case {"t": "Plain" | "Para"}:
                                definition_inlines += _convert_inlines(db.get("c", []))

                            case _:
                                blocks.extend(_convert_block(db))

                    if definition_inlines:
                        merged = merge_text(
                            term + [StyledText(text=" :: ")] + definition_inlines
                        )

                        blocks.append(
                            build(
                                Paragraph,
                                content=inline_nodes_to_content(merged),
                            )
                        )

            return blocks

        case "BlockQuote":
            nested: list[DocumentBlock] = []

            for inner in pb["c"]:
                nested.extend(_convert_block(inner))

            return [
                build(
                    Quote,
                    nested=nested,
                )
            ]

        case "HorizontalRule":
            return []

        case "Table":
            return [_convert_table(pb)]

        case "RawBlock":
            return [
                build(
                    RawBlock,
                    content=pb["c"][1],
                    lang=pb["c"][0],
                )
            ]

        case "Div":
            attr, inner = pb["c"]
            identifier, classes, keyvals = attr

            if "captioned-content" in classes:
                caption: str | None = None
                remaining = inner

                if remaining and remaining[0].get("t") == "Div":
                    cap_attr, cap_blocks = remaining[0]["c"]
                    _cap_identifier, cap_classes, _cap_keyvals = cap_attr

                    if "caption" in cap_classes:
                        caption = _extract_caption_text(cap_blocks)
                        remaining = remaining[1:]

                converted: list[DocumentBlock] = []

                for child in remaining:
                    converted.extend(_convert_block(child))

                if caption and converted:
                    _attach_caption(converted[0], caption)

                return converted

            nested: list[DocumentBlock] = []

            for child in inner:
                nested.extend(_convert_block(child))

            return [
                build(
                    Div,
                    props=DivProps(
                        identifier=identifier,
                        classes=classes,
                        attributes={key: value for key, value in keyvals},
                    ),
                    nested=nested,
                )
            ]

        case _:
            raise ValueError(f"Implicitly handled document block of type '{t}': {pb}")


def _nest_by_heading_level(
    blocks: Sequence[DocumentBlock],
) -> list[DocumentBlock]:
    root: list[DocumentBlock] = []
    heading_stack: list[Heading] = []

    for block in blocks:
        if isinstance(block, Heading):
            level = block.props.level

            while heading_stack and heading_stack[-1].props.level >= level:
                heading_stack.pop()

            if heading_stack:
                heading_stack[-1].nested.append(block)
            else:
                root.append(block)

            heading_stack.append(block)
            continue

        if heading_stack:
            heading_stack[-1].nested.append(block)
        else:
            root.append(block)

    return root


def pandoc_to_document(
    path: Path,
    *,
    reader: str,
) -> Document:
    pandoc = plumbum.local["pandoc"]
    ast = json.loads(
        pandoc(
            "--from",
            reader,
            "--to",
            "json",
            str(path),
        )
    )

    flat_blocks: list[DocumentBlock] = []

    for pb in ast["blocks"]:
        flat_blocks.extend(_convert_block(pb))

    return build(
        Document,
        nested=_nest_by_heading_level(flat_blocks),
    )
