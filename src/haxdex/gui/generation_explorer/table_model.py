from beartype.typing import Any
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QAbstractItemDelegate, QApplication, QTableView, QWidget

from .constants import (
    GenExplorerColumns,
    COLOR_BAD,
    COLOR_REPLACED,
    COLOR_WARN,
    EDITABLE_COLS,
    HEADERS,
)
from .model_registry import ModelRegistry
from .models import GenerationRowState, LoraState


class GenerationTableModel(QAbstractTableModel):

    def __init__(self, rows: list[GenerationRowState], registry: ModelRegistry):
        super().__init__()
        self.rows = rows
        self.registry = registry
        for row in self.rows:
            for lora in row.loras:
                color = self.registry.color_for(lora.model, "lora")
                if color == COLOR_BAD or color == COLOR_WARN:
                    lora.enabled = False

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() in EDITABLE_COLS | {GenExplorerColumns.COL_LORAS.value}:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        row = self.rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.UserRole and col == GenExplorerColumns.COL_LORAS.value:
            return [
                dict(enabled=item.enabled, model=item.model, weight=item.weight)
                for item in row.loras
            ]

        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            match col:
                case GenExplorerColumns.COL_IMAGE.value:
                    return row.path
                case GenExplorerColumns.COL_POSITIVE.value:
                    return row.positive
                case GenExplorerColumns.COL_NEGATIVE.value:
                    return row.negative
                case GenExplorerColumns.COL_WIDTH.value:
                    return row.width
                case GenExplorerColumns.COL_HEIGHT.value:
                    return row.height
                case GenExplorerColumns.COL_STEPS.value:
                    return row.steps
                case GenExplorerColumns.COL_CFG.value:
                    return row.cfg
                case GenExplorerColumns.COL_CHECKPOINT.value:
                    return row.checkpoint
                case GenExplorerColumns.COL_MD5.value:
                    return row.md5
                case _:
                    return None

        if role == Qt.ItemDataRole.BackgroundRole:
            match col:
                case GenExplorerColumns.COL_CHECKPOINT.value:
                    return self.registry.color_for(row.checkpoint, "checkpoint")
                case GenExplorerColumns.COL_LORAS.value:
                    colors = []
                    for lora in row.loras:
                        if not lora.enabled:
                            continue
                        color = self.registry.color_for(lora.model, "lora")
                        if color is not None:
                            colors.append(color)
                    if any(item == COLOR_BAD for item in colors):
                        return COLOR_BAD
                    if any(item == COLOR_WARN for item in colors):
                        return COLOR_WARN
                    if any(item == COLOR_REPLACED for item in colors):
                        return COLOR_REPLACED
                    return None
                case _:
                    return None

        if role == Qt.ItemDataRole.ToolTipRole:
            match col:
                case GenExplorerColumns.COL_POSITIVE.value:
                    return row.positive
                case GenExplorerColumns.COL_NEGATIVE.value:
                    return row.negative
                case GenExplorerColumns.COL_MD5.value:
                    return row.md5
                case _:
                    return None

        return None

    def setData(self,
                index: QModelIndex,
                value: Any,
                role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole:
            return False
        row = self.rows[index.row()]
        col = index.column()
        match col:
            case GenExplorerColumns.COL_POSITIVE.value:
                row.positive = str(value)
            case GenExplorerColumns.COL_NEGATIVE.value:
                row.negative = str(value)
            case GenExplorerColumns.COL_WIDTH.value:
                row.width = int(value)
            case GenExplorerColumns.COL_HEIGHT.value:
                row.height = int(value)
            case GenExplorerColumns.COL_STEPS.value:
                row.steps = int(value)
            case GenExplorerColumns.COL_CFG.value:
                row.cfg = float(value)
            case GenExplorerColumns.COL_CHECKPOINT.value:
                row.checkpoint = str(value)
            case GenExplorerColumns.COL_LORAS.value:
                row.loras = [
                    LoraState(
                        enabled=bool(item.get("enabled", True)),
                        model=str(item.get("model", "")),
                        weight=float(item.get("weight", 1.0)),
                    ) for item in value
                ]
            case _:
                return False
        self.dataChanged.emit(index, index)
        return True


class RunFriendlyTableView(QTableView):

    def active_delegate_editor(self) -> QWidget | None:
        viewport = self.viewport()
        widget = QApplication.focusWidget()
        while widget is not None:
            if widget.parentWidget() is viewport:
                return widget
            widget = widget.parentWidget()
        return None

    def commit_active_editor(self) -> None:
        editor = self.active_delegate_editor()
        if editor is None:
            return
        self.commitData(editor)
        self.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()
        index = self.indexAt(point)
        if index.isValid() and index.column() == 10:
            self.commit_active_editor()
        super().mousePressEvent(event)
