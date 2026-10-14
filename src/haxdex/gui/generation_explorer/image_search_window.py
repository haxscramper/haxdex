import json

from beartype.typing import Any
from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    QSortFilterProxyModel,
    Qt,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from haxdex.services.core.db import IndexDatabase
from haxdex.services.indexers.image_generation import GenerationParamsIndexer
from haxdex.services.indexers.safetensor_indexer import SafetensorIndexer
from haxdex.services.indexers.wd_indexer import WdTagIndexer

from .constants import EDITOR_H, THUMB
from .delegates import ImageDelegate, resolve_image_path
from .models import ExplorerSettings, ImageSearchRecord
from .repository import ExplorerRepository

ROLE_RECORD = int(Qt.ItemDataRole.UserRole)
ROLE_TAGS = ROLE_RECORD + 1


class ImageSearchListModel(QAbstractListModel):
    def __init__(self, rows: list[ImageSearchRecord]):
        super().__init__()
        self.rows = rows

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        row = self.rows[index.row()]
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return row.path
        if role == ROLE_RECORD:
            return row
        if role == ROLE_TAGS:
            return row.searchable_tags
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.path
        return None


class ImageTagFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.exact_tags: set[str] = set()
        self.any_tags: set[str] = set()
        self.exclude_tags: set[str] = set()

    def set_filters(
        self,
        exact_tags: set[str],
        any_tags: set[str],
        exclude_tags: set[str],
    ) -> None:
        self.exact_tags = exact_tags
        self.any_tags = any_tags
        self.exclude_tags = exclude_tags
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source = self.sourceModel()
        index = source.index(source_row, 0, source_parent)
        tags = index.data(ROLE_TAGS) or set()

        if self.exact_tags and not self.exact_tags.issubset(tags):
            return False

        if self.any_tags and tags.isdisjoint(self.any_tags):
            return False

        if self.exclude_tags and not tags.isdisjoint(self.exclude_tags):
            return False

        return True


class TagInputBlock(QFrame):
    def __init__(self, title: str, suggestions: list[str], parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        title_label = QLabel(title, self)
        layout.addWidget(title_label)

        row = QHBoxLayout()
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Enter tag and click Add")
        self.input.setFixedHeight(EDITOR_H)
        self.add_button = QPushButton("Add", self)
        self.add_button.setFixedHeight(EDITOR_H)
        row.addWidget(self.input)
        row.addWidget(self.add_button)
        layout.addLayout(row)

        self.list = QListWidget(self)
        layout.addWidget(self.list)

        self.remove_button = QPushButton("Remove selected", self)
        self.remove_button.setFixedHeight(EDITOR_H)
        layout.addWidget(self.remove_button)

        completer = QCompleter(suggestions, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.input.setCompleter(completer)

        self.add_button.clicked.connect(self.add_current)
        self.input.returnPressed.connect(self.add_current)
        self.remove_button.clicked.connect(self.remove_selected)

    def add_current(self) -> None:
        raw = self.input.text().strip()
        if raw == "":
            return

        parts = [part.strip() for part in raw.split(",")]
        existing = {self.list.item(i).text() for i in range(self.list.count())}

        for part in parts:
            normalized = part.casefold()
            if normalized == "":
                continue
            if normalized in existing:
                continue
            item = QListWidgetItem(normalized)
            self.list.addItem(item)
            existing.add(normalized)

        self.input.clear()

    def remove_selected(self) -> None:
        selected_items = self.list.selectedItems()
        for item in selected_items:
            row = self.list.row(item)
            self.list.takeItem(row)

    def tags(self) -> set[str]:
        output: set[str] = set()
        for index in range(self.list.count()):
            output.add(self.list.item(index).text())
        return output


class ImageOverlayDialog(QDialog):
    def __init__(self, record: ImageSearchRecord, parent=None):
        super().__init__(parent)
        self.record = record

        self.setWindowTitle(record.path)
        self.resize(1600, 900)

        layout = QHBoxLayout(self)

        center_scroll = QScrollArea(self)
        center_scroll.setWidgetResizable(True)
        center_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(center_scroll, stretch=1)

        image_label = QLabel(center_scroll)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_scroll.setWidget(image_label)

        pixmap = QPixmap(resolve_image_path(record.path))
        if pixmap.isNull():
            image_label.setText("Could not load image")
        else:
            image_label.setPixmap(pixmap)

        side = QWidget(self)
        side.setFixedWidth(380)
        side_layout = QVBoxLayout(side)
        layout.addWidget(side)

        copy_path = QPushButton("Copy path", side)
        copy_prompt = QPushButton("Copy prompt", side)
        copy_negative = QPushButton("Copy negative prompt", side)
        copy_structured = QPushButton("Copy structured prompt (JSON)", side)

        copy_path.clicked.connect(self.copy_path)
        copy_prompt.clicked.connect(self.copy_prompt)
        copy_negative.clicked.connect(self.copy_negative_prompt)
        copy_structured.clicked.connect(self.copy_structured)

        side_layout.addWidget(copy_path)
        side_layout.addWidget(copy_prompt)
        side_layout.addWidget(copy_negative)
        side_layout.addWidget(copy_structured)

        side_layout.addWidget(QLabel("Prompt", side))
        prompt_view = QPlainTextEdit(side)
        prompt_view.setReadOnly(True)
        prompt_view.setPlainText(self.prompt_text())
        side_layout.addWidget(prompt_view)

        side_layout.addWidget(QLabel("Negative Prompt", side))
        negative_view = QPlainTextEdit(side)
        negative_view.setReadOnly(True)
        negative_view.setPlainText(self.negative_prompt_text())
        side_layout.addWidget(negative_view)

    def prompt_text(self) -> str:
        generation = self.record.generation_params
        if generation is None:
            return ""
        return generation.positive

    def negative_prompt_text(self) -> str:
        generation = self.record.generation_params
        if generation is None:
            return ""
        return generation.negative

    def structured_payload(self) -> dict[str, Any]:
        generation = self.record.generation_params
        wd_tags = self.record.wd_tags

        return {
            "path": self.record.path,
            "md5": self.record.md5,
            "generation_params": (
                generation.model_dump() if generation is not None else None
            ),
            "wd_tags": (wd_tags.model_dump() if wd_tags is not None else None),
        }

    def copy_path(self) -> None:
        QApplication.clipboard().setText(self.record.path)

    def copy_prompt(self) -> None:
        QApplication.clipboard().setText(self.prompt_text())

    def copy_negative_prompt(self) -> None:
        QApplication.clipboard().setText(self.negative_prompt_text())

    def copy_structured(self) -> None:
        text = json.dumps(self.structured_payload(), indent=2, ensure_ascii=False)
        QApplication.clipboard().setText(text)


class ImageSearchWindow(QWidget):
    def __init__(
        self,
        settings: ExplorerSettings,
        index_database: IndexDatabase,
        generation_params_indexer: GenerationParamsIndexer,
        safetensor_indexer: SafetensorIndexer,
        wd_tag_indexer: WdTagIndexer,
    ):
        super().__init__()
        self.settings = settings

        repository = ExplorerRepository(
            index_database=index_database,
            generation_params_indexer=generation_params_indexer,
            safetensor_indexer=safetensor_indexer,
            wd_tag_indexer=wd_tag_indexer,
        )

        indexed_files = repository.load_indexed_files()
        rows = repository.build_image_search_records(indexed_files)

        all_tags = sorted({tag for row in rows for tag in row.searchable_tags})

        self.model = ImageSearchListModel(rows)
        self.proxy = ImageTagFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.setWindowTitle("Image Search")
        self.resize(1600, 900)

        root_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root_layout.addWidget(splitter)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)

        self.exact_block = TagInputBlock("Exact Match", all_tags, left)
        self.any_block = TagInputBlock("Match Any", all_tags, left)
        self.exclude_block = TagInputBlock("Exclude", all_tags, left)

        left_layout.addWidget(self.exact_block)
        left_layout.addWidget(self.any_block)
        left_layout.addWidget(self.exclude_block)

        self.apply_button = QPushButton("Search", left)
        self.apply_button.setFixedHeight(EDITOR_H)
        left_layout.addWidget(self.apply_button)

        self.result_count = QLabel("", left)
        left_layout.addWidget(self.result_count)
        left_layout.addStretch()

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)

        self.view = QListView(right)
        self.view.setModel(self.proxy)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(8)
        self.view.setGridSize(QSize(THUMB + 16, THUMB + 16))
        self.view.setSelectionMode(QListView.SelectionMode.SingleSelection)

        self.image_delegate = ImageDelegate(self.view)
        self.view.setItemDelegate(self.image_delegate)

        right_layout.addWidget(self.view)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 1180])

        self.apply_button.clicked.connect(self.apply_filters)
        self.view.clicked.connect(self.open_overlay)

        self.apply_filters()

    def apply_filters(self) -> None:
        exact_tags = self.exact_block.tags()
        any_tags = self.any_block.tags()
        exclude_tags = self.exclude_block.tags()

        self.proxy.set_filters(
            exact_tags=exact_tags,
            any_tags=any_tags,
            exclude_tags=exclude_tags,
        )
        self.result_count.setText(f"Results: {self.proxy.rowCount()}")

    def open_overlay(self, proxy_index: QModelIndex) -> None:
        source_index = self.proxy.mapToSource(proxy_index)
        record = source_index.data(ROLE_RECORD)
        if record is None:
            return
        dialog = ImageOverlayDialog(record, self)
        dialog.exec()
