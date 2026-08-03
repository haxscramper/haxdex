import random
from difflib import SequenceMatcher
from pathlib import Path

from PyQt6.QtCore import (
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QEvent,
    pyqtSignal,
    QAbstractItemModel,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QTextOption
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QStyleOptionViewItem,
)

from .constants import (
    GenExplorerColumns,
    COLOR_BAD,
    COLOR_REPLACED,
    COLOR_WARN,
    EDITOR_H,
    IMAGE_EXTS,
    LORA_ON_W,
    LORA_ROW_H,
    LORA_WEIGHT_W,
    MODEL_EXTS,
    RUN_BTN_H,
    RUN_BTN_W,
    THUMB,
)
from .model_registry import ModelRegistry


def resolve_image_path(path: str) -> str:
    value = Path(path)
    if value.suffix.lower() in IMAGE_EXTS and value.exists():
        return str(value)
    for extension in IMAGE_EXTS:
        candidate = value.with_suffix(extension)
        if candidate.exists():
            return str(candidate)
    return path


class ImageLoaderSignals(QObject):
    done = pyqtSignal(str)


class ImageLoader(QRunnable):

    def __init__(self, path: str, cache: dict[str, QImage], signals: ImageLoaderSignals):
        super().__init__()
        self.path = path
        self.cache = cache
        self.signals = signals

    def run(self) -> None:
        real = resolve_image_path(self.path)
        image = QImage(real)
        if not image.isNull():
            image = image.scaled(
                THUMB,
                THUMB,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.cache[self.path] = image
        self.signals.done.emit(self.path)


class ImageDelegate(QStyledItemDelegate):

    def __init__(self, view: QTableView):
        super().__init__(view)
        self.view = view
        self.cache: dict[str, QImage] = {}
        self.pending: set[str] = set()
        self.pool = QThreadPool.globalInstance()
        self.signals = ImageLoaderSignals()
        self.signals.done.connect(self.on_done)

    def on_done(self, path: str) -> None:
        self.pending.discard(path)
        self.view.viewport().update()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(THUMB + 8, THUMB + 8)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        path = index.data(Qt.ItemDataRole.DisplayRole)
        image = self.cache.get(path)
        if image is None:
            if path not in self.pending:
                self.pending.add(path)
                self.pool.start(ImageLoader(path, self.cache, self.signals))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "loading…")
            return
        if image.isNull():
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "no image")
            return
        x = option.rect.x() + (option.rect.width() - image.width()) // 2
        y = option.rect.y() + (option.rect.height() - image.height()) // 2
        painter.drawImage(x, y, image)


class ImagePreviewDialog(QDialog):

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(Path(path).name)
        self.resize(1024, 768)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        from PyQt6.QtWidgets import QScrollArea

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(scroll)

        label = QLabel(scroll)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(label)

        real = resolve_image_path(path)
        pixmap = QPixmap(real)
        if pixmap.isNull():
            label.setText(f"Could not load image:\n{real}")
        else:
            label.setPixmap(pixmap)

        row = QHBoxLayout()
        row.addStretch()
        copy_button = QPushButton("Copy", self)
        copy_button.setFixedHeight(EDITOR_H)
        copy_button.clicked.connect(self.copy_path)
        row.addWidget(copy_button)
        layout.addLayout(row)

    def copy_path(self) -> None:
        QApplication.clipboard().setText(self.path)


class ModelComboDelegate(QStyledItemDelegate):

    def __init__(
        self,
        options: list[tuple[str, str]],
        allow_empty: bool,
        registry: ModelRegistry,
        kind: str,
        filter_by_type: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.options_data = options
        self.allow_empty = allow_empty
        self.registry = registry
        self.kind = kind
        self.filter_by_type = filter_by_type

    def sorted_options(self, default_text: str,
                       options: list[tuple[str, str]]) -> list[tuple[str, str]]:
        needle = default_text.strip().casefold()
        if needle == "":
            return sorted(options, key=lambda item: item[0].casefold())

        def score(item: tuple[str, str]) -> float:
            display, payload = item
            return max(
                SequenceMatcher(None, needle, display.casefold()).ratio(),
                SequenceMatcher(None, needle, payload.casefold()).ratio(),
            )

        return sorted(options, key=lambda item: (-score(item), item[0].casefold()))

    def filtered_options(self, default_text: str,
                         index: QModelIndex) -> list[tuple[str, str]]:
        options = self.options_data
        if self.filter_by_type:
            current = str(index.data(Qt.ItemDataRole.EditRole) or "").strip()
            payload = self.registry.resolve_payload(current, self.kind)
            model_type = self.registry.type_for_payload(payload, self.kind)
            if model_type is not None:
                options = [
                    item for item in options
                    if self.registry.type_for_payload(item[1], self.kind) == model_type
                ]
        return self.sorted_options(default_text, options)

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setMaxVisibleItems(14)
        combo.setMaximumHeight(EDITOR_H)

        default_text = str(
            index.data(Qt.ItemDataRole.DisplayRole) or
            index.data(Qt.ItemDataRole.EditRole) or "").strip()
        if self.allow_empty:
            combo.addItem("", "")

        for display, payload in self.filtered_options(default_text, index):
            combo.addItem(display, payload)

        return combo

    def updateEditorGeometry(self, editor: QWidget, option: QStyleOptionViewItem,
                             index: QModelIndex):
        margin_x = 1
        width = max(0, option.rect.width() - (2 * margin_x))
        height = min(36, max(0, option.rect.height() - 4))
        y = option.rect.y() + (option.rect.height() - height) // 2
        editor.setGeometry(option.rect.x() + margin_x, y, width, height)

    def setEditorData(self, editor: QWidget, index: QModelIndex):
        payload = self.registry.resolve_payload(
            str(index.data(Qt.ItemDataRole.EditRole) or ""), self.kind)
        for row in range(editor.count()):
            if str(editor.itemData(row) or "") == payload:
                editor.setCurrentIndex(row)
                return
        editor.setCurrentText(str(index.data(Qt.ItemDataRole.EditRole) or ""))

    def setModelData(self, editor: QWidget, model: QAbstractItemModel,
                     index: QModelIndex):
        text = editor.currentText().strip()
        payload = editor.currentData()
        if payload:
            model.setData(index, payload, Qt.ItemDataRole.EditRole)
            return
        model.setData(index, self.registry.resolve_payload(text, self.kind),
                      Qt.ItemDataRole.EditRole)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        value = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        payload, replaced = self.registry.resolve_with_replacement(value, self.kind)

        if value == "":
            text = "—"
        elif replaced:
            text = self.registry.display_for_payload(payload, self.kind) or payload
        else:
            text = value

        status = self.registry.color_for(value, self.kind)
        if value == "":
            background = QColor(110, 110, 110, 45)
        elif status == COLOR_BAD:
            background = QColor(220, 80, 80, 60)
        elif status == COLOR_WARN:
            background = QColor(255, 165, 0, 60)
        elif status == COLOR_REPLACED:
            background = QColor(150, 80, 220, 60)
        else:
            background = QColor(70, 150, 90, 50)

        foreground = option.palette.text().color()
        rect = option.rect.adjusted(6, (option.rect.height() - 22) // 2, -6,
                                    -(option.rect.height() - 22) // 2)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(foreground)
        shown = option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideMiddle,
                                              rect.width() - 10)
        painter.drawText(
            rect.adjusted(5, 0, -5, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            shown,
        )
        painter.restore()


class LoraEditorWidget(QWidget):

    def __init__(self,
                 options: list[tuple[str, str]],
                 registry: ModelRegistry,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.options_data = options
        self.registry = registry

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["On", "LoRA", "Weight"])
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0,
                                                           QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1,
                                                           QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,
                                                           QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, LORA_ON_W)
        self.table.setColumnWidth(2, LORA_WEIGHT_W)

        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(LORA_ROW_H)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        add_button = QPushButton("+", self)
        remove_button = QPushButton("-", self)
        add_button.setFixedWidth(24)
        remove_button.setFixedWidth(24)
        add_button.clicked.connect(self.add_row)
        remove_button.clicked.connect(self.remove_row)
        row.addStretch()
        row.addWidget(add_button)
        row.addWidget(remove_button)
        layout.addLayout(row)

    def resort_combo_by_similarity(self, combo: QComboBox, text: str) -> None:
        needle = self.registry.normalize_payload(text)
        if needle == "":
            ranked = self.options_data
        else:
            ranked = sorted(
                self.options_data,
                key=lambda item: SequenceMatcher(None, needle, item[1]).ratio(),
                reverse=True,
            )
        selected = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        for display, payload in ranked:
            combo.addItem(display, payload)
        combo.setCurrentText(selected)
        combo.blockSignals(False)

    def make_combo(self, payload: str = "") -> QComboBox:
        combo = QComboBox(self.table)
        combo.setEditable(True)
        combo.setMaxVisibleItems(14)

        for display, value in self.options_data:
            combo.addItem(display, value)

        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(lambda text, arg_combo=combo: self.
                                         resort_combo_by_similarity(arg_combo, text))

        if payload != "":
            for index in range(combo.count()):
                if combo.itemData(index) == payload:
                    combo.setCurrentIndex(index)
                    return combo
            combo.setCurrentText(payload)
        return combo

    def add_row(self, lora: dict | None = None) -> None:
        value = lora or {"enabled": True, "model": "", "weight": 1.0}
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, LORA_ROW_H)

        on_item = QTableWidgetItem()
        model_value = str(value.get("model", ""))
        color = self.registry.color_for(model_value, "lora")
        missing = color in {COLOR_BAD, COLOR_WARN}

        if missing:
            on_item.setFlags(Qt.ItemFlag.ItemIsSelectable)
            on_item.setCheckState(Qt.CheckState.Unchecked)
        else:
            on_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable |
                             Qt.ItemFlag.ItemIsSelectable)
            if bool(value.get("enabled", True)):
                on_item.setCheckState(Qt.CheckState.Checked)
            else:
                on_item.setCheckState(Qt.CheckState.Unchecked)
        self.table.setItem(row, 0, on_item)

        payload = self.registry.resolve_payload(str(value.get("model", "")), "lora")
        combo = self.make_combo(payload)
        self.table.setCellWidget(row, 1, combo)

        spin_box = QDoubleSpinBox(self.table)
        spin_box.setRange(-10.0, 10.0)
        spin_box.setSingleStep(0.05)
        spin_box.setValue(float(value.get("weight", 1.0)))
        self.table.setCellWidget(row, 2, spin_box)

    def remove_row(self) -> None:
        row = self.table.currentRow()
        if -1 < row:
            self.table.removeRow(row)

    def set_loras(self, loras: list[dict]) -> None:
        self.table.setRowCount(0)
        for lora in loras:
            self.add_row({
                "enabled": bool(lora.get("enabled", True)),
                "model": str(lora.get("model", "")),
                "weight": float(lora.get("weight", 1.0)),
            })
        if self.table.rowCount() == 0:
            self.add_row()

    def loras(self) -> list[dict]:
        output: list[dict] = []
        for row in range(self.table.rowCount()):
            on_item = self.table.item(row, 0)
            enabled = True
            if on_item is not None:
                enabled = on_item.checkState() == Qt.CheckState.Checked

            combo = self.table.cellWidget(row, 1)
            model_text = combo.currentText().strip() if combo is not None else ""
            model_payload = self.registry.resolve_payload(model_text, "lora")
            if model_payload == "":
                continue

            spin_box = self.table.cellWidget(row, 2)
            weight = spin_box.value() if spin_box is not None else 1.0

            output.append({
                "enabled": enabled,
                "model": model_payload,
                "weight": float(weight),
            })
        return output


class LoraDelegate(QStyledItemDelegate):

    def __init__(self, registry: ModelRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry

    def createEditor(self, parent, option, index):
        checkpoint_index = index.sibling(index.row(),
                                         GenExplorerColumns.COL_CHECKPOINT.value)
        checkpoint = str(checkpoint_index.data(Qt.ItemDataRole.EditRole) or "").strip()
        payload = self.registry.resolve_payload(checkpoint, "checkpoint")
        checkpoint_type = self.registry.type_for_payload(payload, "checkpoint")
        if checkpoint_type is None:
            options = self.registry.options("lora")
        else:
            options = [
                item for item in self.registry.options("lora")
                if self.registry.type_for_payload(item[1], "lora") == checkpoint_type
            ]
        return LoraEditorWidget(options, self.registry, parent)

    def setEditorData(self, editor: LoraEditorWidget, index):
        editor.set_loras(index.data(Qt.ItemDataRole.UserRole) or [])

    def setModelData(self, editor: LoraEditorWidget, model, index):
        model.setData(index, editor.loras(), Qt.ItemDataRole.EditRole)

    def paint(self, painter: QPainter, option, index):
        loras = index.data(Qt.ItemDataRole.UserRole) or []
        if loras == []:
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "—")
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect = option.rect.adjusted(4, 4, -4, -4)
        row_height = 22

        on_width = 34
        weight_width = 84
        model_width = max(0, rect.width() - on_width - weight_width)

        x_on = rect.x()
        x_model = x_on + on_width
        x_weight = x_model + model_width

        painter.setPen(option.palette.mid().color())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        for index_value, lora in enumerate(loras):
            y = rect.y() + index_value * row_height
            if rect.bottom() < y:
                break

            row_rect = rect.__class__(rect.x(), y, rect.width(),
                                      min(row_height,
                                          rect.bottom() - y + 1))

            enabled = bool(lora.get("enabled", True))
            raw_model = str(lora.get("model", "") or "")
            payload, replaced = self.registry.resolve_with_replacement(raw_model, "lora")
            if replaced:
                model = self.registry.display_for_payload(payload, "lora") or payload
            else:
                model = raw_model
            weight = float(lora.get("weight", 1.0))

            if not enabled:
                background = QColor(120, 120, 120, 35)
            else:
                status = self.registry.color_for(model, "lora")
                if status == COLOR_BAD:
                    background = QColor(220, 80, 80, 55)
                elif status == COLOR_WARN:
                    background = QColor(255, 165, 0, 55)
                elif status == COLOR_REPLACED:
                    background = QColor(150, 80, 220, 55)
                else:
                    background = QColor(70, 150, 90, 45)

            painter.fillRect(row_rect, background)

            painter.setPen(option.palette.mid().color())
            painter.drawLine(rect.x(), y, rect.right(), y)

            painter.drawLine(x_model, y, x_model, y + row_rect.height() - 1)
            painter.drawLine(x_weight, y, x_weight, y + row_rect.height() - 1)

            painter.setPen(option.palette.text().color())
            on_text = "☑" if enabled else "☐"
            painter.drawText(
                rect.__class__(x_on + 4, y, on_width - 8, row_rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                on_text,
            )

            painter.drawText(
                rect.__class__(x_model + 6, y, model_width - 12, row_rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft |
                Qt.TextFlag.TextSingleLine,
                model,
            )

            painter.drawText(
                rect.__class__(x_weight + 6, y, weight_width - 10, row_rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight |
                Qt.TextFlag.TextSingleLine,
                f"{weight:g}",
            )

        painter.restore()


class PromptEdit(QPlainTextEdit):

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setPlainText(text)
        self.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class PromptDelegate(QStyledItemDelegate):

    def createEditor(self, parent, option, index):
        return PromptEdit(str(index.data(Qt.ItemDataRole.EditRole) or ""), parent)

    def setEditorData(self, editor, index):
        editor.setPlainText(str(index.data(Qt.ItemDataRole.EditRole) or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(2, 2, -2, -2))

    def sizeHint(self, option, index):
        height = max(option.rect.height(), EDITOR_H * 3)
        return QSize(option.rect.width(), height)


class NumberDelegate(QStyledItemDelegate):

    def __init__(self, decimals: bool, parent=None):
        super().__init__(parent)
        self.decimals = decimals

    def createEditor(self, parent, option, index):
        if self.decimals:
            spin_box = QDoubleSpinBox(parent)
            spin_box.setDecimals(3)
            spin_box.setSingleStep(0.05)
        else:
            from PyQt6.QtWidgets import QSpinBox

            spin_box = QSpinBox(parent)
            spin_box.setSingleStep(1)
            spin_box.setMinimum(0)
            spin_box.setMaximum(8192)

        spin_box.setMaximumHeight(EDITOR_H)
        spin_box.setFixedHeight(EDITOR_H)
        return spin_box

    def updateEditorGeometry(self, editor, option, index):
        y = option.rect.y() + (option.rect.height() - EDITOR_H) // 2
        editor.setGeometry(option.rect.x(), y, max(0, option.rect.width()), EDITOR_H)

    def setEditorData(self, editor, index):
        editor.setValue(index.data(Qt.ItemDataRole.EditRole))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class ButtonDelegate(QStyledItemDelegate):
    clicked = pyqtSignal(QModelIndex)

    def __init__(self, view: QTableView, parent=None):
        super().__init__(parent)
        self.view = view
        self.pressed = QPersistentModelIndex()

    def button_rect(self, rect):
        x = rect.x() + (rect.width() - RUN_BTN_W) // 2
        y = rect.y() + (rect.height() - RUN_BTN_H) // 2
        return rect.__class__(x, y, RUN_BTN_W, RUN_BTN_H)

    def paint(self, painter, option, index):
        button = QStyleOptionButton()
        button.rect = self.button_rect(option.rect)
        button.text = "Run"
        button.state = QStyle.StateFlag.State_Enabled
        if self.pressed.isValid() and self.pressed.row() == index.row(
        ) and self.pressed.column() == index.column():
            button.state |= QStyle.StateFlag.State_Sunken
        else:
            button.state |= QStyle.StateFlag.State_Raised
        QApplication.style().drawControl(QStyle.ControlElement.CE_PushButton, button,
                                         painter)

    def editorEvent(self, event, model, option, index):
        rect = self.button_rect(option.rect)

        if event.type() == QEvent.Type.MouseButtonPress and rect.contains(event.pos()):
            self.pressed = QPersistentModelIndex(index)
            self.view.viewport().update(option.rect)
            return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            hit = rect.contains(event.pos())
            was_pressed = self.pressed.isValid() and self.pressed.row() == index.row(
            ) and self.pressed.column() == index.column()
            self.pressed = QPersistentModelIndex()
            self.view.viewport().update(option.rect)
            if was_pressed and hit:
                self.clicked.emit(index)
                return True

        return False
