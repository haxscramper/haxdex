from __future__ import annotations

from dataclasses import dataclass, field
import enum
import io
import logging

from beartype import beartype
from beartype.typing import Callable, List, Tuple
from pydantic import BaseModel, Field

from PyQt6.QtCore import QAbstractItemModel, QAbstractProxyModel, QByteArray, QModelIndex, Qt
from rich import box
from rich.console import Console, Group, RenderableType
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

log = logging.getLogger(__name__)


@beartype
@dataclass
class ModelProxyRecord:
    row: int
    column: int
    identity: str
    model_name: str


@beartype
@dataclass
class IndexRoleRepr:
    role_name: str
    role_value: str


@beartype
@dataclass
class ItemFormatContext:
    row: int
    column: int
    identity: str
    model_name: str
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
    depth: int
    nested_in_cell_depth: int
    row_count: int
    column_count: int
    nested_columns: list[int]
    tree_columns: list[int]


@beartype
@dataclass
class BuildState:
    model_names: dict[int, str] = field(default_factory=dict)


class SnapshotRole(BaseModel):
    role_name: str
    role_value: str


class SnapshotProxy(BaseModel):
    row: int
    column: int
    identity: str
    model_name: str


class SnapshotItem(BaseModel):
    row: int
    column: int
    identity: str
    model_name: str
    depth: int
    proxies: list[SnapshotProxy] = Field(default_factory=list)
    roles: list[SnapshotRole] = Field(default_factory=list)
    final_repr: str = ""


class SnapshotRow(BaseModel):
    cells: list[SnapshotNode] = Field(default_factory=list)


class SnapshotNode(BaseModel):
    item: SnapshotItem | None = None
    depth: int
    row_count: int
    column_count: int
    column_headers: list[str] = Field(default_factory=list)
    nested_columns: list[int] = Field(default_factory=list)
    tree_columns: list[int] = Field(default_factory=list)
    rows: list[SnapshotRow] = Field(default_factory=list)


class ModelDumpSnapshot(BaseModel):
    root_model_name: str
    root: SnapshotNode


SnapshotRow.model_rebuild()
SnapshotNode.model_rebuild()


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
    def extra_roles(
        self,
        model: QAbstractItemModel,
        index: QModelIndex,
    ) -> list[IndexRoleRepr]:
        return [
            IndexRoleRepr(
                role_name="model_size",
                role_value=f"{model.rowCount(index)} rows {model.columnCount(index)} cols",
            )
        ]

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


_MEASURE_CONSOLE = Console(width=10000, file=io.StringIO())


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
def _resolve_model_name(model: QAbstractItemModel | None, state: BuildState) -> str:
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
def _collect_proxy_chain(index: QModelIndex, state: BuildState) -> list[SnapshotProxy]:
    records: list[SnapshotProxy] = []
    current_index = index
    current_model = current_index.model()

    while current_model is not None:
        records.append(
            SnapshotProxy(
                row=current_index.row(),
                column=current_index.column(),
                identity=_index_identity(current_index),
                model_name=_resolve_model_name(current_model, state),
            ))
        if not isinstance(current_model, QAbstractProxyModel):
            break
        mapped = current_model.mapToSource(current_index)
        if not mapped.isValid():
            break
        source_model = current_model.sourceModel()
        current_index = mapped
        if isinstance(source_model, QAbstractItemModel):
            current_model = source_model
        else:
            current_model = None

    return records


@beartype
def _collect_roles(
    model: QAbstractItemModel,
    index: QModelIndex,
    config: ModelRichDumpConfig,
) -> list[SnapshotRole]:
    if not config.include_roles:
        return []

    role_data = model.roleNames()
    records: list[SnapshotRole] = []

    for role, role_name_raw in sorted(role_data.items(), key=lambda entry: int(entry[0])):
        role_int = int(role)
        role_name = config.role_name(role_name_raw)
        if not config.accept_role(role_int, role_name):
            continue
        value = index.data(role_int)
        if value is None:
            continue
        records.append(
            SnapshotRole(
                role_name=role_name,
                role_value=config.format_value(value),
            ))

    for extra in config.extra_roles(model=model, index=index):
        records.append(
            SnapshotRole(
                role_name=extra.role_name,
                role_value=extra.role_value,
            ))

    return records


@beartype
def _column_headers(model: QAbstractItemModel, column_count: int) -> list[str]:
    headers: list[str] = []
    for column in range(column_count):
        header_value = model.headerData(
            column,
            Qt.Orientation.Horizontal,
            int(Qt.ItemDataRole.DisplayRole),
        )
        if header_value is None:
            headers.append(f"C{column}")
        else:
            headers.append(str(header_value))
    return headers


@beartype
def _extract_item(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    state: BuildState,
    config: ModelRichDumpConfig,
    to_string: Callable[[QModelIndex], str] | None,
) -> SnapshotItem | None:
    if not index.isValid():
        return None

    final_repr = ""
    if to_string is not None:
        final_repr = str(to_string(index))

    return SnapshotItem(
        row=index.row(),
        column=index.column(),
        identity=_index_identity(index),
        model_name=_resolve_model_name(model, state),
        depth=depth,
        proxies=_collect_proxy_chain(index=index, state=state),
        roles=_collect_roles(model=model, index=index, config=config),
        final_repr=final_repr,
    )


@beartype
def _extract_structure_columns(
    model: QAbstractItemModel,
    index: QModelIndex,
    row_count: int,
    column_count: int,
) -> tuple[list[int], list[int]]:
    nested_columns: list[int] = []
    tree_columns: list[int] = []

    for column in range(column_count):
        column_has_nested = False
        column_matches_run = False
        for row in range(row_count):
            nested_index = model.index(row, column, index)
            nested_row_count = model.rowCount(nested_index)
            if nested_row_count <= 0:
                continue
            column_has_nested = True
            if model.columnCount(nested_index) == column_count:
                column_matches_run = True
        if column_has_nested:
            nested_columns.append(column)
        if column_matches_run:
            tree_columns.append(column)

    return nested_columns, tree_columns


@beartype
def extract_snapshot(
    model: QAbstractItemModel | None,
    parent: QModelIndex | None = None,
    to_string: Callable[[QModelIndex], str] | None = None,
    config: ModelRichDumpConfig | None = None,
) -> ModelDumpSnapshot:
    if model is None:
        return ModelDumpSnapshot(
            root_model_name="0x0",
            root=SnapshotNode(depth=0, row_count=0, column_count=0),
        )
    if parent is None:
        parent = QModelIndex()

    if config is None:
        config = ModelRichDumpConfig()

    state = BuildState()
    root_model_name = _resolve_model_name(model, state)

    @beartype
    def extract_node(index: QModelIndex, depth: int) -> SnapshotNode:
        assert model is not None
        row_count = model.rowCount(index)
        column_count = model.columnCount(index)
        nested_columns, tree_columns = _extract_structure_columns(
            model=model,
            index=index,
            row_count=row_count,
            column_count=column_count,
        )
        node = SnapshotNode(
            item=_extract_item(
                model=model,
                index=index,
                depth=depth,
                state=state,
                config=config,
                to_string=to_string,
            ),
            depth=depth,
            row_count=row_count,
            column_count=column_count,
            column_headers=_column_headers(model=model, column_count=column_count),
            nested_columns=nested_columns,
            tree_columns=tree_columns,
            rows=[],
        )

        if row_count <= 0:
            return node
        if config.max_depth is not None and config.max_depth < depth:
            return node

        rows: list[SnapshotRow] = []
        for row in range(row_count):
            cells: list[SnapshotNode] = []
            for column in range(column_count):
                nested_index = model.index(row, column, index)
                cells.append(extract_node(nested_index, depth + 1))
            rows.append(SnapshotRow(cells=cells))
        node.rows = rows
        return node

    return ModelDumpSnapshot(
        root_model_name=root_model_name,
        root=extract_node(parent, 0),
    )


@beartype
def _item_context(item: SnapshotItem, depth: int) -> ItemFormatContext:
    return ItemFormatContext(
        row=item.row,
        column=item.column,
        identity=item.identity,
        model_name=item.model_name,
        depth=depth,
        proxies=[
            ModelProxyRecord(
                row=proxy.row,
                column=proxy.column,
                identity=proxy.identity,
                model_name=proxy.model_name,
            ) for proxy in item.proxies
        ],
        roles=[
            IndexRoleRepr(role_name=role.role_name, role_value=role.role_value)
            for role in item.roles
        ],
        final_repr=item.final_repr,
    )


@beartype
def _default_item_renderable(
    context: ItemFormatContext,
    config: ModelRichDumpConfig,
) -> RenderableType:
    parts: list[RenderableType] = []
    label = Text()

    if config.include_proxy_chain:
        for idx, proxy in enumerate(context.proxies):
            if 0 < idx:
                label.append("->", style=config.style_index)
            chunk = Text("[", style=config.style_index)
            chunk.append(f"{proxy.row}:{proxy.column}", style=config.style_index)
            if config.include_index_identity:
                chunk.append(", ", style=config.style_index)
                chunk.append(proxy.identity, style=config.style_index)
            chunk.append(", ", style=config.style_index)
            chunk.append(proxy.model_name, style=config.style_model_name)
            chunk.append("]", style=config.style_index)
            label.append_text(chunk)
    else:
        if config.include_index_coordinates:
            label.append(f"[{context.row}:{context.column}]", style=config.style_index)

        if config.include_index_identity:
            if 0 < len(label.plain):
                label.append(" ", style=config.style_index)

            label.append(context.identity, style=config.style_index)

    parts.append(label)

    if config.include_final_repr and 0 < len(context.final_repr):
        parts.append(Text(context.final_repr, style=config.style_repr))

    if 0 < len(context.roles):
        role_table = config.create_role_table()
        for role in context.roles:
            role_table.add_row(f"{role.role_name} = {role.role_value}")
        parts.append(role_table)

    return Group(*parts)


@beartype
def _format_item_renderable(
    item: SnapshotItem,
    depth: int,
    config: ModelRichDumpConfig,
) -> RenderableType:
    context = _item_context(item=item, depth=depth)
    override = config.format_item(context)
    if override is not None:
        return override
    return _default_item_renderable(context=context, config=config)


@beartype
def _node_structure_context(node: SnapshotNode, depth: int,
                            nested_in_cell_depth: int) -> StructureDetectContext:
    return StructureDetectContext(
        depth=depth,
        nested_in_cell_depth=nested_in_cell_depth,
        row_count=node.row_count,
        column_count=node.column_count,
        nested_columns=node.nested_columns,
        tree_columns=node.tree_columns,
    )


@beartype
def _render_value(
    node: SnapshotNode,
    depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> RenderableType:
    if node.item is None:
        return Text(root_model_name, style=config.style_model_name)
    return _format_item_renderable(item=node.item, depth=depth, config=config)


@beartype
def _render_cell(
    node: SnapshotNode,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> RenderableType:
    if node.row_count <= 0 or config.max_nested_in_cells <= nested_in_cell_depth:
        return _render_value(node=node,
                             depth=depth,
                             config=config,
                             root_model_name=root_model_name)
    return _render_node(
        node=node,
        depth=depth,
        nested_in_cell_depth=nested_in_cell_depth + 1,
        config=config,
        root_model_name=root_model_name,
        suppress_item_at_root=False,
    )


@beartype
def _render_list(
    node: SnapshotNode,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> RenderableType:
    row_count = len(node.rows)
    values: list[RenderableType] = []
    item_width = len("item")
    index_width = max(len("#"), len(str(max(0, row_count - 1))))

    for row_idx, row in enumerate(node.rows):
        if len(row.cells) <= 0:
            raise ValueError(
                f"Row {row_idx} has no cells while rendering list node at depth {depth}")
        value = _render_cell(
            node=row.cells[0],
            depth=depth + 1,
            nested_in_cell_depth=nested_in_cell_depth,
            config=config,
            root_model_name=root_model_name,
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
def _render_table(
    node: SnapshotNode,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> RenderableType:
    row_count = len(node.rows)
    column_count = node.column_count

    rows: list[list[RenderableType]] = []
    column_widths = [
        len(node.column_headers[column])
        if column < len(node.column_headers) else len(f"C{column}")
        for column in range(column_count)
    ]

    for row_idx in range(row_count):
        row = node.rows[row_idx]
        if len(row.cells) != column_count:
            raise ValueError(
                f"Row {row_idx} has {len(row.cells)} cells, expected {column_count} cells for table node at depth {depth}"
            )
        row_renderables: list[RenderableType] = []
        for column in range(column_count):
            cell = _render_cell(
                node=row.cells[column],
                depth=depth + 1,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                root_model_name=root_model_name,
            )
            row_renderables.append(cell)
            column_widths[column] = max(column_widths[column], _measure_width(cell))
        rows.append(row_renderables)

    table = config.create_table()
    for column in range(column_count):
        header = node.column_headers[column] if column < len(
            node.column_headers) else f"C{column}"
        table.add_column(header,
                         width=column_widths[column],
                         no_wrap=True,
                         overflow="ignore")
    for row_renderables in rows:
        table.add_row(*row_renderables)
    return table


@beartype
def _render_tree(
    node: SnapshotNode,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> RenderableType:
    root_label = _render_value(node=node,
                               depth=depth,
                               config=config,
                               root_model_name=root_model_name)
    root_tree = config.create_tree(root_label)

    for row_idx, row in enumerate(node.rows):
        if len(row.cells) <= 0:
            raise ValueError(
                f"Row {row_idx} has no cells while rendering tree node at depth {depth}")
        nested_node = row.cells[0]
        nested_label = _render_value(
            node=nested_node,
            depth=depth + 1,
            config=config,
            root_model_name=root_model_name,
        )
        branch = root_tree.add(nested_label)

        if config.max_depth is not None and config.max_depth < depth:
            continue
        if nested_node.row_count <= 0:
            continue

        branch.add(
            _render_node(
                node=nested_node,
                depth=depth + 1,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                root_model_name=root_model_name,
                suppress_item_at_root=True,
            ))
    return root_tree


@beartype
def _tree_index_line(context: ItemFormatContext, config: ModelRichDumpConfig) -> str:
    if config.include_proxy_chain:
        chunks: list[str] = []
        for proxy in context.proxies:
            part = f"[{proxy.row}:{proxy.column}"
            if config.include_index_identity:
                part += f", {proxy.identity}"
            part += f", {proxy.model_name}]"
            chunks.append(part)
        return "->".join(chunks)

    result = ""
    if config.include_index_coordinates:
        result += f"[{context.row}:{context.column}]"
    if config.include_index_identity:
        if 0 < len(result):
            result += " "
        result += context.identity
    return result


@beartype
def _tree_value_line(context: ItemFormatContext,
                     config: ModelRichDumpConfig) -> list[str]:
    if config.include_final_repr and 0 < len(context.final_repr):
        return [context.final_repr]

    if 0 < len(context.roles):
        result = list()
        name_width = max([1, 1] + [len(role.role_name) for role in context.roles])

        for role in context.roles:
            result.append(f"{role.role_name:<{name_width}} = {role.role_value}")

        return result

    else:
        return [""]


@beartype
def _tree_cell_lines(
    node: SnapshotNode,
    depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> list[str]:
    if node.item is None:
        return ["", root_model_name]

    else:
        context = _item_context(item=node.item, depth=depth)
        return [_tree_index_line(context=context, config=config)] + _tree_value_line(
            context=context, config=config)


@beartype
def _row_is_flat(row: SnapshotRow, visible_columns: int) -> bool:
    for column in range(visible_columns):
        if len(row.cells) <= column:
            raise ValueError(
                f"Row has {len(row.cells)} cells, cannot access column {column} while checking table-tree flatness"
            )
        if 0 < row.cells[column].row_count:
            return False
    return True


@beartype
def _max_table_tree_depth(node: SnapshotNode, column_count: int) -> int:
    row_count = len(node.rows)
    if row_count <= 0:
        return 1

    max_depth = 1
    for row_idx, row in enumerate(node.rows):
        if len(row.cells) <= 0:
            raise ValueError(
                f"Row {row_idx} has no cells while computing table-tree depth")
        first = row.cells[0]
        if first.row_count <= 0:
            continue
        if first.column_count != column_count:
            continue
        max_depth = max(max_depth, 1 + _max_table_tree_depth(first, column_count))
    return max_depth


@beartype
def _make_tree_rows_table(
    node: SnapshotNode,
    rows: list[int],
    depth: int,
    column_count: int,
    widths: list[int],
    leading_width: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
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

    for row_idx in rows:
        if len(node.rows) <= row_idx:
            raise ValueError(
                f"Requested row {row_idx} while table-tree node has {len(node.rows)} rows"
            )
        row = node.rows[row_idx]
        idx_cells: list[RenderableType] = [Text("")]
        val_cells: list[RenderableType] = [Text("")]

        for column in range(column_count):
            if len(row.cells) <= column:
                raise ValueError(
                    f"Row {row_idx} has {len(row.cells)} cells, expected at least {column_count} cells"
                )

            index_line, *value_lines = _tree_cell_lines(
                node=row.cells[column],
                depth=depth,
                config=config,
                root_model_name=root_model_name,
            )

            idx_cells.append(Text(index_line, style=config.style_index))
            val_cells.append(Group(*value_lines))

        table.add_row(*idx_cells)
        table.add_row(*val_cells, end_section=True)

    return table


@beartype
def _collect_tree_table_widths(
    node: SnapshotNode,
    depth: int,
    column_count: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
    widths: list[int],
) -> None:
    row_count = len(node.rows)
    visible_columns = min(column_count, node.column_count)

    for row_idx in range(row_count):
        row = node.rows[row_idx]
        for column in range(visible_columns):
            if len(row.cells) <= column:
                raise ValueError(
                    f"Row {row_idx} has {len(row.cells)} cells, expected at least {visible_columns} cells"
                )
            index_line, *value_lines = _tree_cell_lines(
                node=row.cells[column],
                depth=depth,
                config=config,
                root_model_name=root_model_name,
            )

            widths[column + 1] = max(widths[column + 1], len(index_line),
                                     max([len(value_line) for value_line in value_lines]))

        if visible_columns <= 0:
            continue

        if len(row.cells) <= 0:
            raise ValueError(
                f"Row {row_idx} has no cells while collecting tree-table widths")

        first = row.cells[0]
        if first.row_count <= 0:
            continue

        if first.column_count == column_count:
            _collect_tree_table_widths(
                node=first,
                depth=depth + 1,
                column_count=column_count,
                config=config,
                root_model_name=root_model_name,
                widths=widths,
            )


@beartype
def _append_tree_table_rows(
    node: SnapshotNode,
    parent_tree: Tree,
    depth: int,
    column_count: int,
    widths: list[int],
    config: ModelRichDumpConfig,
    root_model_name: str,
    tree_level: int,
) -> None:
    row_count = len(node.rows)
    visible_columns = min(column_count, node.column_count)

    step = len(config.tree_guide_vertical)
    leading_width = max(config.tree_column_min_width, widths[0] - tree_level * step)

    pending_flat_rows: list[int] = []

    @beartype
    def flush_flat_rows() -> None:
        nonlocal pending_flat_rows
        if len(pending_flat_rows) <= 0:
            return
        parent_tree.add(
            _make_tree_rows_table(
                node=node,
                rows=pending_flat_rows,
                depth=depth,
                column_count=column_count,
                widths=widths,
                leading_width=leading_width,
                config=config,
                root_model_name=root_model_name,
            ))
        pending_flat_rows = []

    for row_idx in range(row_count):
        row = node.rows[row_idx]
        flat = _row_is_flat(row=row, visible_columns=visible_columns)

        if flat:
            pending_flat_rows.append(row_idx)
            continue

        flush_flat_rows()

        branch = parent_tree.add(
            _make_tree_rows_table(
                node=node,
                rows=[row_idx],
                depth=depth,
                column_count=column_count,
                widths=widths,
                leading_width=leading_width,
                config=config,
                root_model_name=root_model_name,
            ))

        if config.max_depth is not None and config.max_depth < depth:
            continue

        for column in range(1, visible_columns):
            if len(row.cells) <= column:
                raise ValueError(
                    f"Row {row_idx} has {len(row.cells)} cells, cannot access nested column {column}"
                )
            nested_node = row.cells[column]
            if nested_node.row_count <= 0:
                continue
            branch.add(
                _render_node(
                    node=nested_node,
                    depth=depth + 1,
                    nested_in_cell_depth=0,
                    config=config,
                    root_model_name=root_model_name,
                    suppress_item_at_root=False,
                ))

        if visible_columns <= 0:
            continue

        if len(row.cells) <= 0:
            raise ValueError(
                f"Row {row_idx} has no cells while appending tree-table rows")
        first = row.cells[0]
        if first.row_count <= 0:
            continue

        if first.column_count == column_count:
            _append_tree_table_rows(
                node=first,
                parent_tree=branch,
                depth=depth + 1,
                column_count=column_count,
                widths=widths,
                config=config,
                root_model_name=root_model_name,
                tree_level=tree_level + 1,
            )
        else:
            branch.add(
                _render_node(
                    node=first,
                    depth=depth + 1,
                    nested_in_cell_depth=0,
                    config=config,
                    root_model_name=root_model_name,
                    suppress_item_at_root=True,
                ))

    flush_flat_rows()


@beartype
def _make_header_table(
    node: SnapshotNode,
    column_count: int,
    widths: list[int],
    config: ModelRichDumpConfig,
) -> Text:
    tree_prefix_width = max(len(config.tree_guide_branch), len(config.tree_guide_last))
    first_col_width = widths[0] + tree_prefix_width

    sep = " | "
    parts: list[str] = [" " * first_col_width]

    for column in range(column_count):
        header = node.column_headers[column] if column < len(
            node.column_headers) else f"C{column}"
        parts.append(f"{header:^{widths[column + 1]}}")

    return Text("  " + sep.join(parts), style="bold")


@beartype
def _render_tree_table(
    node: SnapshotNode,
    depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
) -> RenderableType:
    column_count = node.column_count

    widths = [config.tree_column_min_width]
    for column in range(column_count):
        header = node.column_headers[column] if column < len(
            node.column_headers) else f"C{column}"
        widths.append(len(header))

    max_rel_depth = _max_table_tree_depth(node=node, column_count=column_count)
    widths[0] = max(widths[0], max_rel_depth * len(config.tree_guide_vertical))

    _collect_tree_table_widths(
        node=node,
        depth=depth,
        column_count=column_count,
        config=config,
        root_model_name=root_model_name,
        widths=widths,
    )

    widths = [max(config.tree_column_min_width, width) for width in widths]
    if config.tree_column_max_width is not None:
        widths = [min(width, config.tree_column_max_width) for width in widths]

    root_label = Group(
        Text(root_model_name if node.item is None else node.item.model_name,
             style=config.style_model_name),
        _make_header_table(
            node=node,
            column_count=column_count,
            widths=widths,
            config=config,
        ),
    )
    root = config.create_tree(root_label)

    _append_tree_table_rows(
        node=node,
        parent_tree=root,
        depth=depth,
        column_count=column_count,
        widths=widths,
        config=config,
        root_model_name=root_model_name,
        tree_level=0,
    )

    return root


@beartype
def _render_node(
    node: SnapshotNode,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    root_model_name: str,
    suppress_item_at_root: bool,
) -> RenderableType:
    context = _node_structure_context(node=node,
                                      depth=depth,
                                      nested_in_cell_depth=nested_in_cell_depth)
    structure = config.infer_structure(context)
    log.debug(
        f"structure {structure} at depth {depth} rows {context.row_count} "
        f"columns {context.column_count} nested {context.nested_columns} tree {context.tree_columns}"
    )

    match structure:
        case ModelStructure.VALUE:
            return _render_value(node=node,
                                 depth=depth,
                                 config=config,
                                 root_model_name=root_model_name)
        case ModelStructure.TREE:
            return _render_tree(
                node=node,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                root_model_name=root_model_name,
            )
        case ModelStructure.LIST:
            body = _render_list(
                node=node,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                root_model_name=root_model_name,
            )
        case ModelStructure.TABLE:
            body = _render_table(
                node=node,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                root_model_name=root_model_name,
            )
        case ModelStructure.TABLE_TREE | ModelStructure.COMPLEX_TREE:
            body = _render_tree_table(
                node=node,
                depth=depth,
                config=config,
                root_model_name=root_model_name,
            )
        case _:
            row_repr = node.item.row if node.item is not None else -1
            column_repr = node.item.column if node.item is not None else -1
            raise ValueError(
                f"Unknown structure {structure} for snapshot node [{row_repr}:{column_repr}] at depth {depth}"
            )

    if suppress_item_at_root or node.item is None:
        return body

    return Group(
        _render_value(node=node,
                      depth=depth,
                      config=config,
                      root_model_name=root_model_name),
        body,
    )


@beartype
def render_snapshot(
    snapshot: ModelDumpSnapshot,
    config: ModelRichDumpConfig | None = None,
) -> RenderableType:
    if config is None:
        config = ModelRichDumpConfig()
    return _render_node(
        node=snapshot.root,
        depth=0,
        nested_in_cell_depth=0,
        config=config,
        root_model_name=snapshot.root_model_name,
        suppress_item_at_root=not config.include_root_entry,
    )


@beartype
def dump(
    model: QAbstractItemModel | None,
    parent: QModelIndex | None = None,
    to_string: Callable[[QModelIndex], str] | None = None,
    config: ModelRichDumpConfig | None = None,
) -> RenderableType:
    if config is None:
        config = ModelRichDumpConfig()
    snapshot = extract_snapshot(
        model=model,
        parent=parent,
        to_string=to_string,
        config=config,
    )
    return render_snapshot(snapshot=snapshot, config=config)


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


@beartype
def simple_dump(model: QAbstractItemModel, indentation_step: str = "  ") -> str:

    @beartype
    def format_display_value(value: object) -> str:
        match value:
            case None:
                return "None"
            case str():
                return value
            case _:
                return str(value)

    lines: List[str] = []

    @beartype
    def traverse(parent_index: QModelIndex, path: List[Tuple[int, int]],
                 depth: int) -> None:
        row_count = model.rowCount(parent_index)
        column_count = model.columnCount(parent_index)
        for row in range(row_count):
            for col in range(column_count):
                nested_index = model.index(row, col, parent_index)
                nested_path = [*path, (row, col)]
                path_text = "".join(
                    f"[{path_row}.{path_col}]" for path_row, path_col in nested_path)
                display_value = model.data(nested_index, Qt.ItemDataRole.DisplayRole)
                lines.append(
                    f"{indentation_step * depth}{path_text} {format_display_value(display_value)}"
                )
                traverse(nested_index, nested_path, depth + 1)

    traverse(QModelIndex(), [], 0)
    return "\n".join(lines)
