from __future__ import annotations

from beartype import beartype
from beartype.typing import Callable

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from haxdex.gui.agnostic.model_dump import (
    ItemFormatContext,
    ModelRichDumpConfig,
    ModelStructure,
    StructureDetectContext,
    dump,
    extract_snapshot,
    render_text,
)


@beartype
def _structure(model: QStandardItemModel,
               config: ModelRichDumpConfig | None = None) -> ModelStructure:
    if config is None:
        config = ModelRichDumpConfig()
    snapshot = extract_snapshot(model=model, config=config)
    root = snapshot.root
    return config.infer_structure(
        StructureDetectContext(
            depth=0,
            nested_in_cell_depth=0,
            row_count=root.row_count,
            column_count=root.column_count,
            nested_columns=root.nested_columns,
            tree_columns=root.tree_columns,
        ))


@beartype
def _row(*values: str) -> list[QStandardItem]:
    return [QStandardItem(value) for value in values]


@beartype
def _flat_table_model() -> QStandardItemModel:
    model = QStandardItemModel()
    model.setObjectName("flat_table")
    model.setHorizontalHeaderLabels(["A", "B"])
    model.appendRow(_row("r0c0", "r0c1"))
    model.appendRow(_row("r1c0", "r1c1"))
    return model


@beartype
def _table_tree_model() -> QStandardItemModel:
    model = QStandardItemModel()
    model.setObjectName("table_tree")
    model.setHorizontalHeaderLabels(["name", "kind", "value"])
    root_a = _row("root-a", "kind-a", "value-a")
    root_b = _row("root-b", "kind-b", "value-b")
    root_c = _row("root-c", "kind-c", "value-c")
    model.appendRow(root_a)
    model.appendRow(root_b)
    model.appendRow(root_c)
    nested_a0 = _row("nested-a0", "kind-a0", "value-a0")
    nested_a1 = _row("nested-a1", "kind-a1", "value-a1")
    root_a[0].appendRow(nested_a0)
    root_a[0].appendRow(nested_a1)
    nested_a0[0].appendRow(_row("deep-a00", "kind-a00", "value-a00"))
    return model


@beartype
def _complex_tree_model() -> QStandardItemModel:
    model = _table_tree_model()
    model.setObjectName("complex_tree")
    side = _row("side-b1", "kind-b1", "value-b1")
    model.item(1, 1).appendRow(side)
    model.item(1, 1).appendRow(_row("side-b2", "kind-b2", "value-b2"))
    return model


@beartype
def _nested_table_model() -> QStandardItemModel:
    model = QStandardItemModel()
    model.setObjectName("nested_table")
    model.setHorizontalHeaderLabels(["main", "other"])
    main = QStandardItem("cell-main")
    other = QStandardItem("cell-other")
    model.appendRow([main, other])
    main.appendRow(QStandardItem("nested-list-0"))
    main.appendRow(QStandardItem("nested-list-1"))
    return model


@beartype
def test_detects_flat_table() -> None:
    model = _flat_table_model()
    assert _structure(model) == ModelStructure.TABLE
    text = render_text(dump(model=model))
    assert "A" in text
    assert "B" in text
    assert "r0c0" in text
    assert "r1c1" in text


@beartype
def test_detects_list_model() -> None:
    model = QStandardItemModel()
    model.appendRow(QStandardItem("item-0"))
    model.appendRow(QStandardItem("item-1"))
    assert _structure(model) == ModelStructure.LIST
    text = render_text(dump(model=model))
    assert "item-0" in text
    assert "item-1" in text


@beartype
def test_detects_single_column_tree_model() -> None:
    model = QStandardItemModel()
    root = QStandardItem("root")
    model.appendRow(root)
    root.appendRow(QStandardItem("nested"))
    assert _structure(model) == ModelStructure.TREE
    text = render_text(dump(model=model))
    assert "root" in text
    assert "nested" in text


@beartype
def test_multiple_columns_do_not_short_circuit_into_table() -> None:
    model = _table_tree_model()
    assert 1 < model.columnCount(QModelIndex())
    assert _structure(model) == ModelStructure.TABLE_TREE


@beartype
def test_table_tree_renders_tree_with_aligned_columns() -> None:
    model = _table_tree_model()
    renderable = dump(model=model)
    assert isinstance(renderable, Tree)

    text = render_text(renderable, width=300)
    assert "root-a" in text
    assert "nested-a0" in text
    assert "nested-a1" in text
    assert "deep-a00" in text
    assert "value-a00" in text
    assert "├── " in text or "└── " in text

    root_lines = [
        line for line in text.splitlines()
        if "root-a" in line or "root-b" in line or "root-c" in line
    ]
    value_offsets = {line.index("value-") for line in root_lines if "value-" in line}
    assert len(value_offsets) == 1


@beartype
def test_table_tree_groups_flat_rows_into_table_rows() -> None:
    model = _table_tree_model()
    text = render_text(dump(model=model))
    root_b_line = next(line for line in text.splitlines() if "root-b" in line)
    root_c_line = next(line for line in text.splitlines() if "root-c" in line)
    assert "kind-b" in root_b_line
    assert "value-b" in root_b_line
    assert "kind-c" in root_c_line
    assert "value-c" in root_c_line


@beartype
def test_detects_complex_tree_with_nested_non_zero_column() -> None:
    model = _complex_tree_model()
    assert _structure(model) == ModelStructure.COMPLEX_TREE

    renderable = dump(model=model, config=ModelRichDumpConfig())
    assert isinstance(renderable, Tree)

    text = render_text(renderable, width=300)
    assert "side-b1" in text
    assert "side-b2" in text
    assert "nested-a0" in text


@beartype
def test_complex_tree_keeps_base_column_alignment() -> None:
    table_tree = _table_tree_model()
    complex_tree = _complex_tree_model()

    # Keep model names identical so proxy labels have equal width.
    table_tree.setObjectName("model")
    complex_tree.setObjectName("model")

    table_tree_text = render_text(dump(model=table_tree), width=300)
    complex_tree_text = render_text(dump(model=complex_tree), width=300)

    @beartype
    def offset(text: str, needle: str) -> int:
        return next(line for line in text.splitlines() if needle in line).index(needle)

    assert offset(table_tree_text, "kind-a") == offset(complex_tree_text, "kind-a")
    assert offset(table_tree_text, "value-a") == offset(complex_tree_text, "value-a")
    assert offset(table_tree_text, "deep-a00") == offset(complex_tree_text, "deep-a00")


@beartype
def test_nested_table_cell_structure_with_limit() -> None:
    model = _nested_table_model()
    assert _structure(model) == ModelStructure.TABLE

    with_nested = render_text(
        dump(model=model, config=ModelRichDumpConfig(max_nested_in_cells=1)))
    assert "nested-list-0" in with_nested
    assert "nested-list-1" in with_nested
    assert "cell-other" in with_nested

    without_nested = render_text(
        dump(model=model, config=ModelRichDumpConfig(max_nested_in_cells=0)))
    assert "nested-list-0" not in without_nested
    assert "nested-list-1" not in without_nested
    assert "cell-main" in without_nested


@beartype
def test_max_depth_stops_tree_expansion() -> None:
    model = _table_tree_model()
    text = render_text(dump(model=model, config=ModelRichDumpConfig(max_depth=0)))
    assert "root-a" in text
    assert "nested-a0" not in text
    assert "deep-a00" not in text


@beartype
def test_table_title_is_not_rendered() -> None:
    for model in (_flat_table_model(), _table_tree_model(), _complex_tree_model()):
        text = render_text(dump(model=model), width=300)
        assert "Table" not in text
        assert "Tree" not in text
        assert "List" not in text


@beartype
def test_role_acceptance_override() -> None:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["A", "B"])
    model.setItemRoleNames({int(Qt.ItemDataRole.UserRole) + 1: b"custom"})
    model.appendRow(_row("display", "other"))
    model.setData(model.index(0, 0), "custom-value", int(Qt.ItemDataRole.UserRole) + 1)

    class OnlyDisplayConfig(ModelRichDumpConfig):

        @beartype
        def accept_role(self, role: int, role_name: str) -> bool:
            return role == int(Qt.ItemDataRole.DisplayRole)

    default_text = render_text(dump(model=model, config=ModelRichDumpConfig()))
    filtered_text = render_text(dump(model=model, config=OnlyDisplayConfig()))
    assert "custom-value" in default_text
    assert "custom-value" not in filtered_text


@beartype
def test_item_formatting_override() -> None:
    model = _flat_table_model()

    class OverrideItemConfig(ModelRichDumpConfig):

        @beartype
        def format_item(self, context: ItemFormatContext):
            return Text(f"OVERRIDE:{context.row}:{context.column}")

    text = render_text(dump(model=model, config=OverrideItemConfig()))
    assert "OVERRIDE:0:0" in text
    assert "OVERRIDE:1:1" in text


@beartype
def test_to_string_callback_is_used() -> None:
    model = _table_tree_model()
    to_string: Callable[[QModelIndex],
                        str] = lambda index: f"S{index.row()}{index.column()}"
    text = render_text(dump(model=model, to_string=to_string), width=400)
    assert "S00" in text
    assert "S12" in text
