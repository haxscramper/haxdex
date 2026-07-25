from __future__ import annotations

from beartype import beartype
from beartype.typing import Generator

import pytest
from PyQt6.QtCore import QCoreApplication, QModelIndex, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from rich.console import Console
from rich.text import Text

from haxdex.gui.agnostic.model_dump import ModelRichDumpConfig, dump, render_text


@beartype
def test_detects_table_model() -> None:
    model = QStandardItemModel()
    model.setObjectName("tableModel")
    model.setHorizontalHeaderLabels(["A", "B"])
    model.setRowCount(2)
    model.setColumnCount(2)
    model.setData(model.index(0, 0), "r0c0")
    model.setData(model.index(0, 1), "r0c1")
    model.setData(model.index(1, 0), "r1c0")
    model.setData(model.index(1, 1), "r1c1")
    renderable = dump(model=model, parent=QModelIndex(), config=ModelRichDumpConfig())
    text = render_text(renderable, width=220)
    assert "A" in text
    assert "B" in text
    assert "r0c0" in text
    assert "r1c1" in text


@beartype
def test_nested_structure_in_table_cell_with_limit() -> None:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Main", "Other"])
    model.setRowCount(1)
    model.setColumnCount(2)
    root_item = QStandardItem("cell-main")
    other_item = QStandardItem("cell-other")
    nested_row0_col0 = QStandardItem("nested-r0c0")
    nested_row0_col1 = QStandardItem("nested-r0c1")
    root_item.setColumnCount(2)
    root_item.setRowCount(1)
    root_item.setChild(0, 0, nested_row0_col0)
    root_item.setChild(0, 1, nested_row0_col1)
    model.setItem(0, 0, root_item)
    model.setItem(0, 1, other_item)

    config_with_nested = ModelRichDumpConfig(max_nested_in_cells=2)
    text_with_nested = render_text(
        dump(model=model, parent=QModelIndex(), config=config_with_nested))
    assert "nested-r0c0" in text_with_nested
    assert "nested-r0c1" in text_with_nested

    config_without_nested = ModelRichDumpConfig(max_nested_in_cells=0)
    text_without_nested = render_text(dump(model=model,
                                           parent=QModelIndex(),
                                           config=config_without_nested),
                                      width=220)
    assert "nested-r0c0" not in text_without_nested
    assert "nested-r0c1" not in text_without_nested


@beartype
def test_role_acceptance_override() -> None:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["A"])
    model.setRowCount(1)
    model.setColumnCount(1)
    model.setItemRoleNames({int(Qt.ItemDataRole.UserRole) + 1: b"custom"})
    model.setData(model.index(0, 0), "display", int(Qt.ItemDataRole.DisplayRole))
    model.setData(model.index(0, 0), "custom-value", int(Qt.ItemDataRole.UserRole) + 1)

    class OnlyDisplayConfig(ModelRichDumpConfig):

        @beartype
        def accept_role(self, role: int, role_name: str) -> bool:
            return role == int(Qt.ItemDataRole.DisplayRole)

    default_text = render_text(dump(model=model, config=ModelRichDumpConfig()), width=220)
    filtered_text = render_text(dump(model=model, config=OnlyDisplayConfig()), width=220)
    assert "custom-value" in default_text
    assert "custom-value" not in filtered_text


@beartype
def test_item_formatting_override() -> None:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["A"])
    model.setRowCount(1)
    model.setColumnCount(1)
    model.setData(model.index(0, 0), "value")

    class OverrideItemConfig(ModelRichDumpConfig):

        @beartype
        def format_item(self, context):  # type: ignore[override]
            return Text(f"OVERRIDE:{context.index.row()}:{context.index.column()}")

    text = render_text(dump(model=model, config=OverrideItemConfig()), width=220)
    assert "OVERRIDE:0:0" in text
