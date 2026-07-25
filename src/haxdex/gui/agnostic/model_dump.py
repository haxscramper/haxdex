from __future__ import annotations

from dataclasses import dataclass, field

from beartype import beartype
from beartype.typing import Callable, Literal

from PyQt6.QtCore import QAbstractItemModel, QAbstractProxyModel, QByteArray, QModelIndex, Qt
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
import enum


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


@beartype
@dataclass
class StructureDetectContext:
    model: QAbstractItemModel
    index: QModelIndex
    depth: int
    nested_in_cell_depth: int
    row_count: int
    column_count: int


@beartype
@dataclass
class BuildState:
    model_names: dict[int, str] = field(default_factory=dict)


class ModelStructure(str, enum.Enum):
    LIST = "list"
    TABLE = "table"
    TREE = "tree"
    VALUE = "value"


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
    tree_guide_style: str = "bright_black"
    list_title: str = "List"
    table_title: str = "Table"
    tree_title: str = "Tree"

    @beartype
    def create_table(self, title: str | None = None) -> Table:
        return Table(
            title=title,
            show_header=self.table_show_header,
            show_lines=self.table_show_lines,
            expand=self.table_expand,
            pad_edge=self.table_pad_edge,
        )

    @beartype
    def create_list_table(self, title: str | None = None) -> Table:
        return self.create_table(title=title)

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
    def infer_structure(
        self,
        context: StructureDetectContext,
    ) -> ModelStructure:
        if self.max_depth is not None and self.max_depth < context.depth:
            return ModelStructure.VALUE

        if self.detect_table_models and 1 < context.column_count:
            return ModelStructure.TABLE

        if self.detect_list_models and context.column_count <= 1 and 0 < context.row_count:
            any_nested = False
            for row in range(context.row_count):
                nested_index = context.model.index(row, 0, context.index)
                if 0 < context.model.rowCount(nested_index):
                    any_nested = True
                    break
                if 1 < context.model.columnCount(nested_index):
                    any_nested = True
                    break
            if any_nested:
                if self.detect_tree_models:
                    return ModelStructure.TREE

            else:
                return ModelStructure.LIST

        if self.detect_tree_models and 0 < context.row_count:
            return ModelStructure.TREE

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
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> ItemFormatContext:
    roles = _collect_roles(model=model, index=index, config=config)
    proxies = _collect_proxy_chain(index=index)
    final_repr = ""
    if to_string is not None:
        final_repr = str(to_string(index))
    _ = state
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
        state=state,
        to_string=to_string,
    )
    override = config.format_item(context)
    if override is not None:
        return override
    return _default_item_renderable(context=context, config=config, state=state)


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
def _build_list(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
) -> RenderableType:
    table = config.create_list_table(title=config.list_title)
    table.add_column("#", style=config.style_index, no_wrap=True)
    table.add_column("Item")
    row_count = model.rowCount(index)
    for row in range(row_count):
        nested_index = model.index(row, 0, index)
        cell_renderable = _build_node(
            model=model,
            index=nested_index,
            depth=depth + 1,
            nested_in_cell_depth=nested_in_cell_depth,
            config=config,
            state=state,
            to_string=to_string,
            in_table_cell=True,
        )
        table.add_row(str(row), cell_renderable)
    return table


@beartype
def _column_header(model: QAbstractItemModel, column: int) -> str:
    header_value = model.headerData(column, Qt.Orientation.Horizontal,
                                    int(Qt.ItemDataRole.DisplayRole))
    if header_value is None:
        return f"C{column}"
    return str(header_value)


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
    table = config.create_table(title=config.table_title)
    column_count = model.columnCount(index)
    for column in range(column_count):
        table.add_column(_column_header(model, column))
    row_count = model.rowCount(index)
    for row in range(row_count):
        row_renderables: list[RenderableType] = []
        for column in range(column_count):
            nested_index = model.index(row, column, index)
            row_renderables.append(
                _build_node(
                    model=model,
                    index=nested_index,
                    depth=depth + 1,
                    nested_in_cell_depth=nested_in_cell_depth + 1,
                    config=config,
                    state=state,
                    to_string=to_string,
                    in_table_cell=True,
                ))
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
    root_label: RenderableType
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
        model_name = _resolve_model_name(model, state)
        root_label = Text(f"{config.tree_title} [{model_name}]",
                          style=config.style_model_name)
    root_tree = config.create_tree(root_label)
    row_count = model.rowCount(index)
    for row in range(row_count):
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
        if config.max_depth is not None and config.max_depth < depth + 1:
            continue
        nested_row_count = model.rowCount(nested_index)
        if nested_row_count <= 0:
            continue
        nested_renderable = _build_node(
            model=model,
            index=nested_index,
            depth=depth + 2,
            nested_in_cell_depth=nested_in_cell_depth,
            config=config,
            state=state,
            to_string=to_string,
            in_table_cell=False,
            suppress_item_at_root=True,
        )
        branch.add(nested_renderable)
    return root_tree


@beartype
def _build_node(
    model: QAbstractItemModel,
    index: QModelIndex,
    depth: int,
    nested_in_cell_depth: int,
    config: ModelRichDumpConfig,
    state: BuildState,
    to_string: Callable[[QModelIndex], str] | None,
    in_table_cell: bool,
    suppress_item_at_root: bool = False,
) -> RenderableType:
    if in_table_cell and config.max_nested_in_cells <= nested_in_cell_depth:
        return _build_value(
            model=model,
            index=index,
            depth=depth,
            config=config,
            state=state,
            to_string=to_string,
        )
    row_count = model.rowCount(index)
    column_count = model.columnCount(index)
    structure = config.infer_structure(
        StructureDetectContext(
            model=model,
            index=index,
            depth=depth,
            nested_in_cell_depth=nested_in_cell_depth,
            row_count=row_count,
            column_count=column_count,
        ))
    match structure:
        case ModelStructure.TABLE:
            table_renderable = _build_table(
                model=model,
                index=index,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            if suppress_item_at_root:
                return table_renderable
            item_renderable = _build_value(
                model=model,
                index=index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            return Group(item_renderable, table_renderable)
        case ModelStructure.LIST:
            list_renderable = _build_list(
                model=model,
                index=index,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            if suppress_item_at_root:
                return list_renderable
            item_renderable = _build_value(
                model=model,
                index=index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            return Group(item_renderable, list_renderable)
        case ModelStructure.TREE:
            if suppress_item_at_root:
                nested_tree = _build_tree(
                    model=model,
                    index=index,
                    depth=depth,
                    nested_in_cell_depth=nested_in_cell_depth,
                    config=config,
                    state=state,
                    to_string=to_string,
                )
                return nested_tree
            tree_renderable = _build_tree(
                model=model,
                index=index,
                depth=depth,
                nested_in_cell_depth=nested_in_cell_depth,
                config=config,
                state=state,
                to_string=to_string,
            )
            return tree_renderable
        case _:
            return _build_value(
                model=model,
                index=index,
                depth=depth,
                config=config,
                state=state,
                to_string=to_string,
            )


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
    state = BuildState()
    if config.include_root_entry:
        return _build_node(
            model=model,
            index=parent,
            depth=0,
            nested_in_cell_depth=0,
            config=config,
            state=state,
            to_string=to_string,
            in_table_cell=False,
        )
    row_count = model.rowCount(parent)
    column_count = model.columnCount(parent)
    structure = config.infer_structure(
        StructureDetectContext(
            model=model,
            index=parent,
            depth=0,
            nested_in_cell_depth=0,
            row_count=row_count,
            column_count=column_count,
        ))
    match structure:
        case ModelStructure.TABLE:
            return _build_table(
                model=model,
                index=parent,
                depth=0,
                nested_in_cell_depth=0,
                config=config,
                state=state,
                to_string=to_string,
            )
        case ModelStructure.LIST:
            return _build_list(
                model=model,
                index=parent,
                depth=0,
                nested_in_cell_depth=0,
                config=config,
                state=state,
                to_string=to_string,
            )
        case ModelStructure.TREE:
            return _build_tree(
                model=model,
                index=parent,
                depth=0,
                nested_in_cell_depth=0,
                config=config,
                state=state,
                to_string=to_string,
            )
        case _:
            return _build_value(
                model=model,
                index=parent,
                depth=0,
                config=config,
                state=state,
                to_string=to_string,
            )
