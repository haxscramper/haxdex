import argparse
import sys
from pathlib import Path

from beartype import beartype
from beartype.typing import Any
from haxdex_read_media.resources.wd_tagger import WdTaggerResult
from hstd_py_qt.file_view.column_model import AbstractColumnItemModel, ColumnSpec
from hstd_py_qt.file_view.column_sort_filter_proxy import ColumnSortFilterProxyModel
from hstd_py_qt.file_view.configurable_widget import Configurable
from pydantic import BaseModel, Field
from PyQt6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QLocale,
    QModelIndex,
    QObject,
    QRectF,
    QRegularExpression,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFocusEvent,
    QFont,
    QImage,
    QImageReader,
    QPainter,
    QPaintEvent,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMainWindow,
    QPlainTextEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from haxdex.gui.common.qt_model_roles import CustomModelRole

CAPTION_BOX_COLOR_PROPERTY = 0x100001


class CaptionPreviewRow(BaseModel, extra="forbid"):
    path: Path
    text_caption: str
    wd_tags: WdTaggerResult | None = None


class HighlightPattern(BaseModel, extra="forbid"):
    color: str
    regexp: str


class CaptionPreviewConfig(BaseModel, extra="forbid"):
    directory: Path

    caption_suffix: str = ".txt"
    failed_caption_suffix: str = ".txt.failed"
    wd_tags_suffix: str = ".booru.json"

    highlight_patterns: list[HighlightPattern] = Field(default_factory=list)

    show_wd_tags: bool = True
    wd_tags: list[str] = Field(default_factory=list)
    wd_sort_descending: bool = True
    wd_score_precision: int = Field(default=3, ge=0)

    window_title: str = "Tag dataset"
    window_width: int = Field(default=1100, gt=0)
    window_height: int = Field(default=800, gt=0)

    image_column_title: str = "Image"
    caption_column_title: str = "Caption"
    wd_column_title: str = "Tag probability sum"

    image_column_width: int = Field(default=520, gt=0)
    wd_column_width: int = Field(default=200, gt=0)
    row_height: int = Field(default=400, gt=0)

    preview_width: int = Field(default=480, gt=0)
    preview_height: int = Field(default=360, gt=0)
    image_padding: int = Field(default=4, ge=0)
    image_loading_text: str = "loading…"

    caption_font_family: str | None = None
    caption_font_size: int = Field(default=18, gt=0)
    caption_editor_margin: int = Field(default=4, ge=0)
    highlight_box_width: int = Field(default=2, gt=0)


@beartype
def collect_rows(config: CaptionPreviewConfig) -> list[CaptionPreviewRow]:
    directory = config.directory.expanduser().resolve()
    if not directory.is_dir():
        raise NotADirectoryError(
            f"Cannot collect caption previews from {directory}: "
            "the configured input directory does not exist or is not a directory"
        )

    extensions = {
        f".{bytes(image_format).decode('ascii').lower()}"
        for image_format in QImageReader.supportedImageFormats()
    }
    images = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in extensions
        ),
        key=lambda path: path.name.casefold(),
    )

    rows: list[CaptionPreviewRow] = []
    for image in images:
        caption = image.with_suffix(config.caption_suffix)
        failed_caption = image.with_suffix(config.failed_caption_suffix)
        wd_path = image.with_suffix(config.wd_tags_suffix)

        if caption.exists():
            text_caption = caption.read_text()
        elif failed_caption.exists():
            text_caption = failed_caption.read_text()
        else:
            text_caption = ""

        wd_tags = (
            WdTaggerResult.model_validate_json(wd_path.read_text())
            if wd_path.exists()
            else None
        )
        rows.append(
            CaptionPreviewRow(
                path=image,
                text_caption=text_caption,
                wd_tags=wd_tags,
            )
        )

    return rows


@beartype
def compile_patterns(
    config: CaptionPreviewConfig,
) -> list[tuple[QRegularExpression, QTextCharFormat]]:
    patterns: list[tuple[QRegularExpression, QTextCharFormat]] = []

    for position, specification in enumerate(config.highlight_patterns):
        color = QColor(specification.color)
        if not color.isValid():
            raise ValueError(
                f"highlight_patterns[{position}].color is "
                f"{specification.color!r}: expected a valid Qt color"
            )

        expression = QRegularExpression(specification.regexp)
        if not expression.isValid():
            raise ValueError(
                f"highlight_patterns[{position}].regexp is "
                f"{specification.regexp!r}: invalid regular expression at offset "
                f"{expression.patternErrorOffset()}: {expression.errorString()}"
            )

        text_format = QTextCharFormat()
        text_format.setForeground(color)
        patterns.append((expression, text_format))

    return patterns


@beartype
class ImageLoaderSignals(QObject):
    done = pyqtSignal(object, int, int, QImage, str)


@beartype
class ImageLoader(QRunnable):
    def __init__(
        self,
        path: Path,
        size: QSize,
        signals: ImageLoaderSignals,
    ) -> None:
        super().__init__()
        self.path = path
        self.size = QSize(size)
        self.signals = signals

    def run(self) -> None:
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        original_size = reader.size()

        if original_size.isValid():
            reader.setScaledSize(
                original_size.scaled(
                    self.size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                )
            )

        image = reader.read()
        error = reader.errorString() if image.isNull() else ""

        if not image.isNull():
            image = image.scaled(
                self.size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        self.signals.done.emit(
            self.path,
            self.size.width(),
            self.size.height(),
            image,
            error,
        )


@beartype
class ImageDelegate(QStyledItemDelegate):
    def __init__(
        self,
        view: QTableView,
        config: CaptionPreviewConfig,
    ) -> None:
        super().__init__(view)
        self.view = view
        self.config = config
        self.cache: dict[tuple[Path, int, int], QImage] = {}
        self.pending: set[tuple[Path, int, int]] = set()
        self.pool = QThreadPool.globalInstance()
        self.signals = ImageLoaderSignals(self)
        self.signals.done.connect(self.on_done)

    def on_done(
        self,
        path: Path,
        width: int,
        height: int,
        image: QImage,
        error: str,
    ) -> None:
        key = (path, width, height)
        self.pending.remove(key)

        if image.isNull():
            raise ValueError(
                f"Cannot create a {width}×{height} preview for {path}: {error}"
            )

        self.cache[key] = image
        self.view.viewport().update()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        padding = self.config.image_padding * 2
        return QSize(
            self.config.preview_width + padding,
            self.config.preview_height + padding,
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""

        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(
            style.ControlElement.CE_ItemViewItem,
            background,
            painter,
            widget,
        )

        path = index.data(Qt.ItemDataRole.DisplayRole)
        width = self.config.preview_width
        height = self.config.preview_height
        key = (path, width, height)
        image = self.cache.get(key)

        if image is None:
            if key not in self.pending:
                self.pending.add(key)
                self.pool.start(ImageLoader(path, QSize(width, height), self.signals))

            painter.drawText(
                option.rect,
                Qt.AlignmentFlag.AlignCenter,
                self.config.image_loading_text,
            )
            return

        padding = self.config.image_padding
        available = option.rect.adjusted(padding, padding, -padding, -padding)
        if available.width() <= 0 or available.height() <= 0:
            return

        target_size = image.size().scaled(
            available.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        target = QRectF(
            available.x() + (available.width() - target_size.width()) / 2,
            available.y() + (available.height() - target_size.height()) / 2,
            target_size.width(),
            target_size.height(),
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, image)
        painter.restore()


@beartype
class CaptionEditor(QPlainTextEdit):
    editingFinished = pyqtSignal()

    def __init__(
        self,
        config: CaptionPreviewConfig,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.box_width = config.highlight_box_width

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.editingFinished.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())

        block = self.firstVisibleBlock()
        viewport_bottom = self.viewport().height()

        while block.isValid():
            block_rect = self.blockBoundingGeometry(block).translated(
                self.contentOffset()
            )

            if viewport_bottom < block_rect.top():
                break

            if block.isVisible() and 0 <= block_rect.bottom():
                layout = block.layout()

                for format_range in layout.formats():
                    color = format_range.format.property(CAPTION_BOX_COLOR_PROPERTY)
                    if color is None:
                        continue

                    pen = QPen(color)
                    pen.setWidth(self.box_width)
                    painter.setPen(pen)

                    match_start = format_range.start
                    match_end = match_start + format_range.length

                    for line_index in range(layout.lineCount()):
                        line = layout.lineAt(line_index)
                        start = max(match_start, line.textStart())
                        end = min(
                            match_end,
                            line.textStart() + line.textLength(),
                        )

                        if end <= start:
                            continue

                        start_x = line.cursorToX(start)[0]
                        end_x = line.cursorToX(end)[0]

                        rect = QRectF(
                            block_rect.left() + min(start_x, end_x),
                            block_rect.top() + line.y(),
                            abs(end_x - start_x),
                            line.height(),
                        )

                        # Keep the configured stroke inside the line's bounds.
                        if (
                            self.box_width < rect.width()
                            and self.box_width < rect.height()
                        ):
                            inset = self.box_width / 2
                            painter.drawRect(
                                rect.adjusted(inset, inset, -inset, -inset)
                            )

            block = block.next()

        painter.end()


@beartype
class CaptionHighlighter(QSyntaxHighlighter):
    def __init__(
        self,
        document: QTextDocument,
        patterns: list[tuple[QRegularExpression, QTextCharFormat]],
    ) -> None:
        super().__init__(document)
        self.patterns = patterns

    def highlightBlock(self, text: str) -> None:
        for expression, text_format in self.patterns:
            boxed_format = QTextCharFormat(text_format)
            boxed_format.setProperty(
                CAPTION_BOX_COLOR_PROPERTY,
                text_format.foreground().color(),
            )

            matches = expression.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(
                    match.capturedStart(),
                    match.capturedLength(),
                    boxed_format,
                )


@beartype
class CaptionDelegate(QStyledItemDelegate):
    def __init__(
        self,
        config: CaptionPreviewConfig,
        patterns: list[tuple[QRegularExpression, QTextCharFormat]],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.patterns = patterns

    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> CaptionEditor:
        editor = CaptionEditor(self.config, parent)
        font = QFont()
        if self.config.caption_font_family is not None:
            font.setFamily(self.config.caption_font_family)
        font.setPointSize(self.config.caption_font_size)
        editor.setFont(font)

        editor.highlighter = CaptionHighlighter(
            editor.document(),
            self.patterns,
        )
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.editingFinished.connect(lambda: self.commitData.emit(editor))
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        if isinstance(editor, CaptionEditor):
            text = index.data(Qt.ItemDataRole.EditRole)
            if editor.toPlainText() != text:
                editor.setPlainText(text)

    def setModelData(
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex,
    ) -> None:
        if isinstance(editor, CaptionEditor):
            model.setData(
                index,
                editor.toPlainText(),
                Qt.ItemDataRole.EditRole,
            )

    def updateEditorGeometry(
        self,
        editor: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        margin = self.config.caption_editor_margin
        editor.setGeometry(option.rect.adjusted(margin, margin, -margin, -margin))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, CaptionEditor):
            return False
        return super().eventFilter(watched, event)


@beartype
class ProbabilityDelegate(QStyledItemDelegate):
    def __init__(
        self,
        config: CaptionPreviewConfig,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.config = config

    def displayText(self, value: Any, locale: QLocale) -> str:
        if value is None:
            return ""
        return locale.toString(float(value), "f", self.config.wd_score_precision)


@beartype
class PreviewColumn(ColumnSpec):
    def __init__(
        self,
        title: str,
        delegate: QAbstractItemDelegate,
    ) -> None:
        self.title = title
        self.delegate = delegate

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.title
        return None

    def getDelegate(self) -> QAbstractItemDelegate:
        return self.delegate


@beartype
class ImageColumn(PreviewColumn):
    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        row = index.internalPointer()

        match role:
            case Qt.ItemDataRole.DisplayRole:
                return row.path
            case Qt.ItemDataRole.ToolTipRole:
                return str(row.path)
            case CustomModelRole.SortDataRole.value:
                return row.path.name.casefold()
            case _:
                return None


@beartype
class CaptionColumn(PreviewColumn):
    def __init__(
        self,
        config: CaptionPreviewConfig,
        delegate: CaptionDelegate,
    ) -> None:
        super().__init__(config.caption_column_title, delegate)
        self.config = config

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        row = index.internalPointer()

        match role:
            case (
                Qt.ItemDataRole.DisplayRole
                | Qt.ItemDataRole.EditRole
                | CustomModelRole.SortDataRole.value
            ):
                return row.text_caption
            case Qt.ItemDataRole.ToolTipRole:
                return str(row.path.with_suffix(self.config.caption_suffix))
            case Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            case _:
                return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return super().flags(index) | Qt.ItemFlag.ItemIsEditable

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole:
            return False

        row = index.internalPointer()
        caption = row.path.with_suffix(self.config.caption_suffix)

        if not isinstance(value, str):
            raise TypeError(
                f"Cannot save caption {caption}: expected a string, "
                f"received {type(value).__name__}"
            )

        if value == row.text_caption:
            return False

        caption.write_text(value)
        row.text_caption = value
        return True


@beartype
class WdTagsColumn(PreviewColumn):
    def __init__(
        self,
        config: CaptionPreviewConfig,
        delegate: ProbabilityDelegate,
    ) -> None:
        super().__init__(config.wd_column_title, delegate)
        self.selected_tags = frozenset(config.wd_tags)

    def probability_sum(self, row: CaptionPreviewRow) -> float:
        if row.wd_tags is None:
            return 0.0

        return sum(
            (
                tag.probability
                for tag in row.wd_tags.tags
                if tag.name in self.selected_tags
            ),
            start=0.0,
        )

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        row = index.internalPointer()

        match role:
            case (
                Qt.ItemDataRole.DisplayRole
                | Qt.ItemDataRole.EditRole
                | CustomModelRole.SortDataRole.value
            ):
                return self.probability_sum(row)
            case Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
            case _:
                return None


@beartype
class DatasetModel(AbstractColumnItemModel):
    def __init__(
        self,
        rows: list[CaptionPreviewRow],
        columns: list[ColumnSpec],
        parent: QObject,
    ) -> None:
        super().__init__(columns=columns, parent=parent)
        self.rows = rows

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if parent.isValid():
            return QModelIndex()
        if row < 0 or len(self.rows) <= row:
            return QModelIndex()
        if column < 0 or len(self.columns) <= column:
            return QModelIndex()
        return self.createIndex(row, column, self.rows[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        return QModelIndex()

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Vertical:
            if role == Qt.ItemDataRole.DisplayRole:
                return section + 1
            return None

        return super().headerData(section, orientation, role)

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        changed = super().setData(index, value, role)
        if changed:
            self.dataChanged.emit(
                index,
                index,
                [Qt.ItemDataRole.DisplayRole],
            )
        return changed


@beartype
class DatasetTable(QTableView):
    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.pixelDelta().y()
        scrollbar = self.verticalScrollBar()

        if 0 < delta:
            scrollbar.setValue(scrollbar.value() - 1)
        elif delta < 0:
            scrollbar.setValue(scrollbar.value() + 1)

        event.accept()


@beartype
def compare_equal(left: Any, right: Any) -> int:
    return 0


@beartype
def compare_probabilities(left: Any, right: Any) -> int:
    if left < right:
        return -1
    if right < left:
        return 1
    return 0


@beartype
class DatasetWindow(QMainWindow, Configurable[CaptionPreviewConfig]):
    def __init__(self, config: CaptionPreviewConfig) -> None:
        super().__init__()

        self.table = DatasetTable(self)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.table.setWordWrap(False)
        self.setCentralWidget(self.table)

        self.proxy = ColumnSortFilterProxyModel(self)
        self.dataset_model: DatasetModel | None = None
        self.caption_delegate: CaptionDelegate | None = None

        self.image_delegate = ImageDelegate(self.table, config)
        self.probability_delegate = ProbabilityDelegate(config, self.table)

        configure_action = QAction("Configure…", self)
        configure_action.triggered.connect(self.on_configure)
        self.menuBar().addAction(configure_action)

        self.initialize_config(
            config,
            parent=self,
            apply=self.apply_config,
        )

    def on_configure(self, checked: bool) -> None:
        self.show_config_dialog()

    def commit_editors(self) -> None:
        if self.caption_delegate is None or self.table.model() is None:
            return

        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, 1)
            editor = self.table.indexWidget(index)
            if isinstance(editor, CaptionEditor):
                self.caption_delegate.setModelData(
                    editor,
                    self.proxy,
                    index,
                )

    def apply_config(self, config: CaptionPreviewConfig) -> None:
        patterns = compile_patterns(config)
        self.commit_editors()
        rows = collect_rows(config)

        old_model = self.dataset_model
        old_caption_delegate = self.caption_delegate

        self.table.setModel(None)
        self.proxy.sort(-1)

        self.image_delegate.config = config
        self.probability_delegate.config = config
        self.caption_delegate = CaptionDelegate(config, patterns, self.table)

        columns: list[ColumnSpec] = [
            ImageColumn(config.image_column_title, self.image_delegate),
            CaptionColumn(config, self.caption_delegate),
        ]
        if config.show_wd_tags:
            columns.append(WdTagsColumn(config, self.probability_delegate))

        self.dataset_model = DatasetModel(rows, columns, self)
        self.proxy.setSourceModel(self.dataset_model)

        # Other columns compare equal so their priority cannot override the score.
        for column in range(self.dataset_model.columnCount()):
            self.proxy.setSortRule(column, compare_equal)

        if config.show_wd_tags:
            self.proxy.setSortRule(2, compare_probabilities)
            order = (
                Qt.SortOrder.DescendingOrder
                if config.wd_sort_descending
                else Qt.SortOrder.AscendingOrder
            )
            self.proxy.sort(2, order)
        else:
            self.proxy.sort(-1)

        self.table.setModel(self.proxy)
        self.dataset_model.configureView(self.table)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, config.image_column_width)

        if config.show_wd_tags:
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(2, config.wd_column_width)

        self.table.verticalHeader().setDefaultSectionSize(config.row_height)
        self.setWindowTitle(
            f"{config.window_title} — {config.directory.expanduser().resolve()}"
        )
        self.resize(config.window_width, config.window_height)

        for row in range(self.proxy.rowCount()):
            self.table.openPersistentEditor(self.proxy.index(row, 1))

        if old_model is not None:
            old_model.deleteLater()
        if old_caption_delegate is not None:
            old_caption_delegate.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.commit_editors()
        super().closeEvent(event)


@beartype
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview and edit image captions using a JSON configuration."
    )
    parser.add_argument("config", type=Path)
    arguments = parser.parse_args()

    config_path = arguments.config.expanduser().resolve()
    config = CaptionPreviewConfig.model_validate_json(config_path.read_text())

    directory = config.directory.expanduser()
    if not directory.is_absolute():
        directory = config_path.parent / directory
    config.directory = directory.resolve()

    application = QApplication([sys.argv[0]])
    window = DatasetWindow(config)
    window.show()
    try:
        result = application.exec()

        return result
    finally:
        Path(config_path).write_text(window.config.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.exit(main())
