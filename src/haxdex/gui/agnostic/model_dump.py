from __future__ import annotations

from dataclasses import dataclass, field

from beartype import beartype
from beartype.typing import Callable

from PyQt6.QtCore import QAbstractItemModel, QAbstractProxyModel, QByteArray, QModelIndex, Qt
from rich.console import Console, Group, RenderableType
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich import box

import enum
import io
import logging

log = logging.getLogger(__name__)


@beartype
@dataclass
class ModelProxyRecord:
    index: QModelIndex


@beartype
@dataclass
class IndexRoleRepr:
    role_name: str
    role_value: str


@beartype
@dataclass
class ItemFormatContext:
    index: QModelIndex
    depth: int
    proxies: list[ModelProxyRecord]
    roles: list[IndexRoleRepr]
    final_repr: str


class ModelStructure(str, enum.Enum):
    VALUE = "value"
    LIST = "list"
    TABLE = "table"
    TREE = "tree"
    TABLE_TREE = "table_tree"
    COMPLEX_TREE = "complex_tree"


@beartype
@dataclass
class StructureDetectContext:
    model: QAbstractItemModel
    index: QModelIndex
    depth: int
    nested_in_cell_depth: int
    row_count: int
    column_count: int
    nested_columns: list[int]
    tree_columns: list[int]


@beartype
@dataclass
class TreeTableLine:
    prefix: str
    cells: list[RenderableType | None]
    nested: RenderableType | None = None


@beartype
@dataclass
class BuildState:
    model_names: dict[int, str] = field(default_factory=dict)


@beartype
@dataclass
class ModelRichDumpConfig:
    max_depth: int | None = None
    max_nested_in_cells: int = 1
    detect_table_models: bool = True
    detect_list_models: bool = True
    detect_tree_models: bool = True
    include_roles: bool = True
    include_proxy_chain: bool = True
    include_final_repr: bool = True
    include_index_coordinates: bool = True
    include_index_identity: bool = True
    include_root_entry: bool = False
    style_index: str = "cyan"
    style_model_name: str = "yellow"
    style_role_name: str = "magenta"
    style_role_value: str = "green"
    style_repr: str = "white"
    style_error: str = "bold red"
    table_show_header: bool = True
    table_show_lines: bool = True
    table_expand: bool = False
    table_pad_edge: bool = True
    tree_table_show_lines: bool = False
    tree_guide_style: str = "bright_black"
    tree_guide_branch: str = "├── "
    tree_guide_last: str = "└── "
    tree_guide_vertical: str = "│   "
    tree_guide_space: str = "    "
    tree_nested_header: str = "nested"
    tree_column_min_width: int = 1
    tree_column_max_width: int | None = None

    @beartype
    def create_table(self) -> Table:
        return Table(
            show_header=self.table_show_header,
            show_lines=self.table_show_lines,
            expand=self.table_expand,
            pad_edge=self.table_pad_edge,
        )

    @beartype
    def create_list_table(self) -> Table:
        return self.create_table()

    @beartype
    def create_tree_table(self) -> Table:
        return Table(
            show_header=self.table_show_header,
            show_lines=self.tree_table_show_lines,
            expand=False,
            pad_edge=self.table_pad_edge,
        )

    @beartype
    def create_tree(self, label: RenderableType) -> Tree:
        return Tree(label, guide_style=self.tree_guide_style)

    @beartype
    def create_role_table(self) -> Table:
        table = Table.grid(expand=False)
        table.add_column(justify="right", style=self.style_role_name, no_wrap=True)
        table.add_column(style=self.style_role_value, overflow="fold")
        return table

    @beartype
    def role_name(self, role_name: QByteArray) -> str:
        return bytes(role_name).decode("utf-8", errors="replace")

    @beartype
    def accept_role(self, role: int, role_name: str) -> bool:
        if role == int(Qt.ItemDataRole.DisplayRole):
            return True

        if role == int(Qt.ItemDataRole.WhatsThisRole):
            return True

        if int(Qt.ItemDataRole.UserRole) <= role:
            return True

        return False

    @beartype
    def infer_structure(self, context: StructureDetectContext) -> ModelStructure:
        if self.max_depth is not None and self.max_depth < context.depth:
            return ModelStructure.VALUE

        if context.row_count <= 0:
            return ModelStructure.VALUE

        if context.column_count <= 1:
            if 0 < len(context.nested_columns) and self.detect_tree_models:
                return ModelStructure.TREE

            if self.detect_list_models:
                return ModelStructure.LIST

            return ModelStructure.VALUE

        if 0 < len(context.tree_columns) and self.detect_tree_models:
            if context.tree_columns == [0] and context.nested_columns == [0]:
                return ModelStructure.TABLE_TREE

            return ModelStructure.COMPLEX_TREE

        if self.detect_table_models:
            return ModelStructure.TABLE

        return ModelStructure.VALUE

    @beartype
    def format_value(self, value: object) -> str:
        match value:
            case str():
                return value
            case _:
                return repr(value)

    @beartype
    def format_item(self, context: ItemFormatContext) -> RenderableType | None:
        return None


_MEASURE_CONSOLE = Console(width=10_000, file=io.StringIO())


@beartype
def _measure_width(renderable: object) -> int:
    measurement = Measurement.get(_MEASURE_CONSOLE, _MEASURE_CONSOLE.options, renderable)
    return measurement.maximum


@beartype
def _index_identity(index: QModelIndex) -> str:
    if not index.isValid():
        return "0x0"
    return f"0x{index.internalId():x}"


@beartype
def _resolve_model_name(
    model: QAbstractItemModel | None,
    state: BuildState,
) -> str:
    if model is None:
        return "0x0"
    model_id = id(model)
    if model_id in state.model_names:
        return state.model_names[model_id]
    object_name = model.objectName()
    if 0 < len(object_name):
        state.model_names[model_id] = object_name
        return object_name
    generated = f"M{len(state.model_names)}"
    state.model_names[model_id] = generated
    return generated


@beartype
def _collect_proxy_chain(index: QModelIndex) -> list[ModelProxyRecord]:
    records: list[ModelProxyRecord] = []
    current_index = index
    records.append(ModelProxyRecord(index=current_index))
    current_model = current_index.model()
    while isinstance(current_model, QAbstractProxyModel):
        mapped = current_model.mapToSource(current_index)
        if not mapped.isValid():
            break
        current_index = mapped
        records.append(ModelProxyRecord(index=current_index))
        source_model = current_model.sourceModel()
        if isinstance(source_model, QAbstractProxyModel):
            current_model = source_model
        else:
            current_model = None
    return records


@beartype
def _collect_roles(
    model: QAbstractItemModel,
    index: QModelIndex,
    config: ModelRichDumpConfig,
) -> list[IndexRoleRepr]:
    if not config.include_roles:
        return []
    role_data = model.roleNames()
    records: list[IndexRoleRepr] = []
    for role, role_name_raw in sorted(role_data.items(), key=lambda entry: int(entry[0])):
        role_int = int(role)
        role_name = config.role_name(role_name_raw)
        if not config.accept_role(role_int, role_name):
            continue

        value = index.data(role_int)
        if value is None:
            continue

        records.append(
            IndexRoleRepr(role_name=role_name, role_value=config.format_value(value)))

    return records


@beartype
def _default_item_renderable(
    context: ItemFormatContext,
    config: ModelRichDumpConfig,
    state: BuildState,
) -> RenderableType:
    parts: list[RenderableType] = []
    label = Text()
    if config.include_proxy_chain:
        proxy_chunks: list[Text] = []
        for proxy in context.proxies:
            proxy_index = proxy.index
            proxy_model_name = _resolve_model_name(proxy_index.model(), state)
            chunk = Text("[", style=config.style_index)
            chunk.append(f"{proxy_index.row()}:{proxy_index.column()}",
                         style=config.style_index)
            if config.include_index_identity:
                chunk.append(", ", style=config.style_index)
                chunk.append(_index_identity(proxy_index), style=config.style_index)
            chunk.append(", ", style=config.style_index)
            chunk.append(proxy_model_name, style=config.style_model_name)
            chunk.append("]", style=config.style_index)
            proxy_chunks.append(chunk)
        for idx, chunk in enumerate(proxy_chunks):
            if 0 < idx:
                label.append("->", style=config.style_index)
            label.append_text(chunk)
    else:
        if config.include_index_coordinates:
            label.append(f"[{context.index.row()}:{context.index.column()}]",
                         style=config.style_index)
        if config.include_index_identity:
            if 0 < len(label.plain):
                label.append(" ", style=config.style_index)
            label.append(_index_identity(context.index), style=config.style_index)
    parts.append(label)
    if config.include_final_repr and 0 < len(context.final_repr):
        parts.append(Text(context.final_repr, style=config.style_repr))
    if 0 < len(context.roles):
        role_table = config.create_role_table()
        for role in context.roles:
            role_table.add_row(role.role_name, role.role_value)
        parts.append(role_table)
    return Group(*parts)


@beartype
def _item_context(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    config: ModelRichDumpConfig,
    to_string: Callable[[QModelIndex], str] | None,
) -> ItemFormatContext:
    roles = _collect_roles(model=model, index=index, config=config)
    proxies = _collect_proxy_chain(index=index)
    final_repr = ""
    if to_string is not None:
        final_repr = str(to_string(index))
    return ItemFormatContext(
        index=index,
        depth=depth,
        proxies=proxies,
        roles=roles,
        final_repr=final_repr,
    )


@beartype
def _format_item_renderable(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    context = _item_context(
        model=model,
        index=index,
        depth=depth,
        config=config,
        to_string=to_string,
    )
    override = config.format_item(context)
    if override is not None:
        return override
    return _default_item_renderable(context=context, config=config, state=state)


@beartype
def structure_context(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
) -> StructureDetectContext:
    row_count = model.rowCount(index)
    column_count = model.columnCount(index)
    nested_columns: list[int] = []
    tree_columns: list[int] = []
    for column in range(column_count):
        column_has_nested = False
        column_matches_run = False
        for row in range(row_count):
            nested_index = model.index(row, column, index)
            if model.rowCount(nested_index) <= 0:
                continue
            column_has_nested = True
            if model.columnCount(nested_index) == column_count:
                column_matches_run = True
        if column_has_nested:
            nested_columns.append(column)
        if column_matches_run:
            tree_columns.append(column)
    return StructureDetectContext(
        model=model,
        index=index,
        depth=depth,
        nested_in_cell_depth=nested_in_cell_depth,
        row_count=row_count,
        column_count=column_count,
        nested_columns=nested_columns,
        tree_columns=tree_columns,
    )


@beartype
def _column_header(model: QAbstractItemModel, column: int) -> str:
    header_value = model.headerData(column, Qt.Orientation.Horizontal,
                                    int(Qt.ItemDataRole.DisplayRole))
    if header_value is None:
        return f"C{column}"
    return str(header_value)


@beartype
def _build_value(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    return _format_item_renderable(
        model=model,
        index=index,
        depth=depth,
        config=config,
        state=state,
        to_string=to_string,
    )


@beartype
def _build_cell(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    if model.rowCount(index) <= 0 or config.max_nested_in_cells <= nested_in_cell_depth:
        return _build_value(
            model=model,
            index=index,
            depth=depth,
            config=config,
            state=state,
            to_string=to_string,
        )

    return _build_node(
        model=model,
        index=index,
        depth=depth,
        nested_in_cell_depth=nested_in_cell_depth + 1,
        config=config,
        state=state,
        to_string=to_string,
        suppress_item_at_root=False,
    )


@beartype
def _build_list(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    row_count = model.rowCount(index)
    values: list[RenderableType] = []
    item_width = len("item")
    index_width = max(len("#"), len(str(max(0, row_count - 1))))

    for row in range(row_count):
        nested_index = model.index(row, 0, index)
        value = _build_cell(
            model=model,
            index=nested_index,
            depth=depth + 1,
            nested_in_cell_depth=nested_in_cell_depth,
            config=config,
            state=state,
            to_string=to_string,
        )
        values.append(value)
        item_width = max(item_width, _measure_width(value))

    table = config.create_list_table()
    table.add_column("#",
                     style=config.style_index,
                     no_wrap=True,
                     width=index_width,
                     overflow="ignore")
    table.add_column("item", no_wrap=True, width=item_width, overflow="ignore")

    for row, value in enumerate(values):
        table.add_row(str(row), value)

    return table


@beartype
def _build_table(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    row_count = model.rowCount(index)
    column_count = model.columnCount(index)

    rows: list[list[RenderableType]] = []
    column_widths = [len(_column_header(model, column)) for column in range(column_count)]

    for row in range(row_count):
        row_renderables: list[RenderableType] = []
        for column in range(column_count):
            nested_index = model.index(row, column, index)
            cell = _build_cell(
                model=model,
                index=nested_index,
                depth=depth + 1,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            row_renderables.append(cell)
            column_widths[column] = max(column_widths[column], _measure_width(cell))
        rows.append(row_renderables)

    table = config.create_table()
    for column in range(column_count):
        table.add_column(
            _column_header(model, column),
            width=column_widths[column],
            no_wrap=True,
            overflow="ignore",
        )

    for row_renderables in rows:
        table.add_row(*row_renderables)

    return table


@beartype
def _build_tree(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    if index.isValid():
        root_label = _format_item_renderable(
            model=model,
            index=index,
            depth=depth,
            config=config,
            state=state,
            to_string=to_string,
        )
    else:
        root_label = Text(_resolve_model_name(model, state),
                          style=config.style_model_name)
    root_tree = config.create_tree(root_label)
    for row in range(model.rowCount(index)):
        nested_index = model.index(row, 0, index)
        nested_label = _format_item_renderable(
            model=model,
            index=nested_index,
            depth=depth + 1,
            config=config,
            state=state,
            to_string=to_string,
        )
        branch = root_tree.add(nested_label)
        if config.max_depth is not None and config.max_depth < depth:
            continue

        if model.rowCount(nested_index) <= 0:
            continue

        branch.add(
            _build_node(
                model=model,
                index=nested_index,
                depth=depth + 1,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
                suppress_item_at_root=True,
            ))
    return root_tree


@beartype
def _tree_cell_width_hint(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    config: ModelRichDumpConfig,
    to_string: Callable[[QModelIndex], str] | None,
) -> int:
    context = _item_context(
        model=model,
        index=index,
        depth=depth,
        config=config,
        to_string=to_string,
    )

    override = config.format_item(context)
    if override is not None:
        return _measure_width(override)

    candidates: list[int] = [0]

    if config.include_final_repr and 0 < len(context.final_repr):
        candidates.append(len(context.final_repr))

    if config.include_roles:
        for role in context.roles:
            candidates.append(len(role.role_value))
            candidates.append(len(role.role_name) + len(role.role_value))

    if max(candidates) == 0:
        candidates.append(len(f"{index.row()}:{index.column()}"))

    return max(candidates)


@beartype
def _make_aligned_row_table(
    cells: list[RenderableType],
    widths: list[int],
) -> Table:
    row_table = Table.grid(expand=False, pad_edge=False)
    for width in widths:
        row_table.add_column(width=width, no_wrap=True, overflow="ignore")
    row_table.add_row(*cells)
    return row_table


@beartype
def _make_header_table(
    model: QAbstractItemModel,
    column_count: int,
    widths: list[int],
) -> Table:
    header_table = Table.grid(expand=False, pad_edge=False)
    for width in widths:
        header_table.add_column(width=width, no_wrap=True, overflow="ignore")

    header_cells: list[RenderableType] = [Text("")]
    header_cells.extend(
        Text(_column_header(model, column), style="bold")
        for column in range(column_count))
    header_table.add_row(*header_cells)
    return header_table


@beartype
def _tree_index_line(
    context: ItemFormatContext,
    config: ModelRichDumpConfig,
    state: BuildState,
) -> str:
    if config.include_proxy_chain:
        chunks: list[str] = []
        for proxy in context.proxies:
            proxy_index = proxy.index
            proxy_model_name = _resolve_model_name(proxy_index.model(), state)
            part = f"[{proxy_index.row()}:{proxy_index.column()}"
            if config.include_index_identity:
                part += f", {_index_identity(proxy_index)}"
            part += f", {proxy_model_name}]"
            chunks.append(part)
        return "->".join(chunks)

    result = ""
    if config.include_index_coordinates:
        result += f"[{context.index.row()}:{context.index.column()}]"
    if config.include_index_identity:
        if 0 < len(result):
            result += " "
        result += _index_identity(context.index)
    return result


@beartype
def _tree_value_line(context: ItemFormatContext, config: ModelRichDumpConfig) -> str:
    if config.include_final_repr and 0 < len(context.final_repr):
        return context.final_repr

    if 0 < len(context.roles):
        display_roles = [r for r in context.roles if r.role_name == "display"]
        if 0 < len(display_roles):
            return display_roles[0].role_value
        return context.roles[0].role_value

    return ""


@beartype
def _tree_cell_lines(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> tuple[str, str]:
    context = _item_context(
        model=model,
        index=index,
        depth=depth,
        config=config,
        to_string=to_string,
    )
    return (
        _tree_index_line(context=context, config=config, state=state),
        _tree_value_line(context=context, config=config),
    )


@beartype
def _row_is_flat(
    model: QAbstractItemModel,
    parent: QModelIndex,
    row: int,
    visible_columns: int,
) -> bool:
    for column in range(visible_columns):
        idx = model.index(row, column, parent)
        if 0 < model.rowCount(idx):
            return False
    return True


@beartype
def _max_table_tree_depth(
    model: QAbstractItemModel,
    index: QModelIndex,
    column_count: int,
) -> int:
    row_count = model.rowCount(index)
    if row_count <= 0:
        return 1

    max_depth = 1
    for row in range(row_count):
        first_index = model.index(row, 0, index)
        if model.rowCount(first_index) <= 0:
            continue
        if model.columnCount(first_index) != column_count:
            continue
        max_depth = max(
            max_depth,
            1 + _max_table_tree_depth(
                model=model,
                index=first_index,
                column_count=column_count,
            ),
        )
    return max_depth


@beartype
def _make_tree_rows_table(
    model: QAbstractItemModel,
    parent: QModelIndex,
    rows: list[int],
    depth: int,
    column_count: int,
    widths: list[int],
    leading_width: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> Table:
    table = Table(
        show_header=False,
        show_lines=False,
        expand=False,
        pad_edge=config.table_pad_edge,
        box=box.ASCII,
    )

    local_widths = [leading_width, *widths[1:]]
    for width in local_widths:
        table.add_column(width=width, no_wrap=True, overflow="crop")

    for row in rows:
        idx_cells: list[RenderableType] = [Text("")]
        val_cells: list[RenderableType] = [Text("")]

        for column in range(column_count):
            current_index = model.index(row, column, parent)
            index_line, value_line = _tree_cell_lines(
                model=model,
                index=current_index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            idx_cells.append(Text(index_line, style=config.style_index))
            val_cells.append(Text(value_line, style=config.style_repr))

        table.add_row(*idx_cells)
        table.add_row(*val_cells, end_section=True)

    return table


@beartype
def _collect_tree_table_widths(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    column_count: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
    widths: list[int],
) -> None:
    row_count = model.rowCount(index)
    local_column_count = model.columnCount(index)
    visible_columns = min(column_count, local_column_count)

    for row in range(row_count):
        for column in range(visible_columns):
            current_index = model.index(row, column, index)
            index_line, value_line = _tree_cell_lines(
                model=model,
                index=current_index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            widths[column + 1] = max(
                widths[column + 1],
                len(index_line),
                len(value_line),
            )

        if visible_columns <= 0:
            continue

        first_index = model.index(row, 0, index)
        if model.rowCount(first_index) <= 0:
            continue

        if model.columnCount(first_index) == column_count:
            _collect_tree_table_widths(
                model=model,
                index=first_index,
                depth=depth + 1,
                column_count=column_count,
                config=config,
                state=state,
                to_string=to_string,
                widths=widths,
            )


@beartype
def _append_tree_table_rows(
    model: QAbstractItemModel,
    parent_tree: Tree,
    index: QModelIndex,
    depth: int,
    column_count: int,
    widths: list[int],
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
    tree_level: int,
) -> None:
    row_count = model.rowCount(index)
    local_column_count = model.columnCount(index)
    visible_columns = min(column_count, local_column_count)

    step = len(config.tree_guide_vertical)
    leading_width = max(
        config.tree_column_min_width,
        widths[0] - tree_level * step,
    )

    pending_flat_rows: list[int] = []

    def flush_flat_rows() -> None:
        nonlocal pending_flat_rows
        if len(pending_flat_rows) == 0:
            return
        parent_tree.add(
            _make_tree_rows_table(
                model=model,
                parent=index,
                rows=pending_flat_rows,
                depth=depth,
                column_count=column_count,
                widths=widths,
                leading_width=leading_width,
                config=config,
                state=state,
                to_string=to_string,
            ))
        pending_flat_rows = []

    for row in range(row_count):
        flat = _row_is_flat(
            model=model,
            parent=index,
            row=row,
            visible_columns=visible_columns,
        )

        if flat:
            pending_flat_rows.append(row)
            continue

        flush_flat_rows()

        branch = parent_tree.add(
            _make_tree_rows_table(
                model=model,
                parent=index,
                rows=[row],
                depth=depth,
                column_count=column_count,
                widths=widths,
                leading_width=leading_width,
                config=config,
                state=state,
                to_string=to_string,
            ))

        if config.max_depth is not None and config.max_depth < depth:
            continue

        for column in range(1, visible_columns):
            nested_index = model.index(row, column, index)
            if model.rowCount(nested_index) <= 0:
                continue
            branch.add(
                _build_node(
                    model=model,
                    index=nested_index,
                    depth=depth + 1,
                    nested_in_cell_depth=0,
                    config=config,
                    state=state,
                    to_string=to_string,
                    suppress_item_at_root=False,
                ))

        if visible_columns <= 0:
            continue

        first_index = model.index(row, 0, index)
        if model.rowCount(first_index) <= 0:
            continue

        if model.columnCount(first_index) == column_count:
            _append_tree_table_rows(
                model=model,
                parent_tree=branch,
                index=first_index,
                depth=depth + 1,
                column_count=column_count,
                widths=widths,
                config=config,
                state=state,
                to_string=to_string,
                tree_level=tree_level + 1,
            )
        else:
            branch.add(
                _build_node(
                    model=model,
                    index=first_index,
                    depth=depth + 1,
                    nested_in_cell_depth=0,
                    config=config,
                    state=state,
                    to_string=to_string,
                    suppress_item_at_root=True,
                ))

    flush_flat_rows()


@beartype
def _build_tree_table(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    column_count = model.columnCount(index)

    widths = [config.tree_column_min_width]
    widths.extend(len(_column_header(model, column)) for column in range(column_count))

    max_rel_depth = _max_table_tree_depth(
        model=model,
        index=index,
        column_count=column_count,
    )
    widths[0] = max(widths[0], max_rel_depth * len(config.tree_guide_vertical))

    _collect_tree_table_widths(
        model=model,
        index=index,
        depth=depth,
        column_count=column_count,
        config=config,
        state=state,
        to_string=to_string,
        widths=widths,
    )

    widths = [max(config.tree_column_min_width, w) for w in widths]

    if config.tree_column_max_width is not None:
        widths = [min(w, config.tree_column_max_width) for w in widths]

    root_label = Group(
        Text(_resolve_model_name(model, state), style=config.style_model_name),
        _make_header_table(model=model, column_count=column_count, widths=widths),
    )
    root = config.create_tree(root_label)

    _append_tree_table_rows(
        model=model,
        parent_tree=root,
        index=index,
        depth=depth,
        column_count=column_count,
        widths=widths,
        config=config,
        state=state,
        to_string=to_string,
        tree_level=0,
    )

    return root


@beartype
def _build_node(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
    suppress_item_at_root: bool,
) -> RenderableType:
    context = structure_context(
        model=model,
        index=index,
        depth=depth,
        nested_in_cell_depth=nested_in_cell_depth,
    )
    structure = config.infer_structure(context)
    log.debug(f"structure {structure} at depth {depth} "
              f"rows {context.row_count} columns {context.column_count} "
              f"nested {context.nested_columns} tree {context.tree_columns}")

    match structure:
        case ModelStructure.VALUE:
            return _build_value(
                model=model,
                index=index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )
        case ModelStructure.TREE:
            return _build_tree(
                model=model,
                index=index,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
        case ModelStructure.LIST:
            body = _build_list(
                model=model,
                index=index,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
        case ModelStructure.TABLE:
            body = _build_table(
                model=model,
                index=index,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
        case ModelStructure.TABLE_TREE | ModelStructure.COMPLEX_TREE:
            body = _build_tree_table(
                model=model,
                index=index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )
        case _:
            raise ValueError(f"Unknown structure {structure} for index "
                             f"[{index.row()}:{index.column()}] at depth {depth}")

    if suppress_item_at_root or not index.isValid():
        return body

    return Group(
        _build_value(
            model=model,
            index=index,
            depth=depth,
            config=config,
            state=state,
            to_string=to_string,
        ), body)


@beartype
def dump(
    model: QAbstractItemModel | None,
    parent: QModelIndex | None = None,
    to_string: Callable[[QModelIndex], str] | None = None,
    config: ModelRichDumpConfig | None = None,
) -> RenderableType:
    if model is None:
        return Text("")
    if parent is None:
        parent = QModelIndex()
    if config is None:
        config = ModelRichDumpConfig()
    return _build_node(
        model=model,
        index=parent,
        depth=0,
        nested_in_cell_depth=0,
        config=config,
        state=BuildState(),
        to_string=to_string,
        suppress_item_at_root=not config.include_root_entry,
    )


@beartype
def render_text(renderable: object, width: int = 220) -> str:
    required_width = _measure_width(renderable) + 2
    console = Console(
        record=True,
        width=max(width, required_width),
        file=io.StringIO(),
    )
    console.print(renderable)
    return console.export_text()
