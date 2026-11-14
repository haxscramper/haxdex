import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
from pathlib import Path

from beartype import beartype
from hstd_py_qt.file_view.configurable_widget import Configurable
from pydantic import BaseModel, ConfigDict, DirectoryPath
from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSignalBlocker,
    QSortFilterProxyModel,
    Qt,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)


class FileColumn(IntEnum):
    NAME = 0
    MODIFIED = 1
    SIZE = 2


class SortDirection(str, Enum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


class DirectoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: DirectoryPath
    sort_column: FileColumn = FileColumn.NAME
    sort_direction: SortDirection = SortDirection.ASCENDING


@dataclass(frozen=True)
class FileEntry:
    name: str
    modified_ns: int
    size: int


@beartype
class DirectoryModel(QAbstractTableModel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.directory: Path | None = None
        self.entries: list[FileEntry] = []

    def set_directory(self, directory: Path) -> None:
        entries: list[FileEntry] = []

        for path in directory.iterdir():
            if path.is_file():
                stat = path.stat()
                entries.append(
                    FileEntry(
                        name=path.name,
                        modified_ns=stat.st_mtime_ns,
                        size=stat.st_size,
                    )
                )

        self.beginResetModel()
        self.directory = directory
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(FileColumn)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid():
            return None

        entry = self.entries[index.row()]
        column = FileColumn(index.column())

        if role == Qt.ItemDataRole.UserRole:
            match column:
                case FileColumn.NAME:
                    return entry.name
                case FileColumn.MODIFIED:
                    return entry.modified_ns
                case FileColumn.SIZE:
                    return entry.size

        if role == Qt.ItemDataRole.DisplayRole:
            match column:
                case FileColumn.NAME:
                    return entry.name
                case FileColumn.MODIFIED:
                    modified = datetime.fromtimestamp(entry.modified_ns / 1_000_000_000)
                    return modified.strftime("%Y-%m-%d %H:%M:%S")
                case FileColumn.SIZE:
                    return f"{entry.size:,}"

        if role == Qt.ItemDataRole.TextAlignmentRole and column == FileColumn.SIZE:
            return (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter).value

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)

        match FileColumn(section):
            case FileColumn.NAME:
                return "File name"
            case FileColumn.MODIFIED:
                return "Modified"
            case FileColumn.SIZE:
                return "Size (bytes)"

        return None


@beartype
class DirectoryTableView(QTableView, Configurable[DirectoryConfig]):
    def __init__(
        self,
        config: DirectoryConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.source_model = DirectoryModel(self)
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.proxy_model.setSortRole(Qt.ItemDataRole.UserRole)
        self.setModel(self.proxy_model)

        self.setSortingEnabled(True)
        self.horizontalHeader().setSectionResizeMode(
            int(FileColumn.NAME),
            QHeaderView.ResizeMode.Stretch,
        )
        self.horizontalHeader().setSectionResizeMode(
            int(FileColumn.MODIFIED),
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.horizontalHeader().setSectionResizeMode(
            int(FileColumn.SIZE),
            QHeaderView.ResizeMode.ResizeToContents,
        )

        self.initialize_config(
            config,
            parent=self,
            apply=self.apply_directory_config,
        )

        self.horizontalHeader().sortIndicatorChanged.connect(
            self.sort_indicator_changed
        )

    def apply_directory_config(self, config: DirectoryConfig) -> None:
        if self.source_model.directory != config.directory:
            self.source_model.set_directory(config.directory)

        order = (
            Qt.SortOrder.AscendingOrder
            if config.sort_direction == SortDirection.ASCENDING
            else Qt.SortOrder.DescendingOrder
        )

        with QSignalBlocker(self.horizontalHeader()):
            self.horizontalHeader().setSortIndicator(
                int(config.sort_column),
                order,
            )
            self.proxy_model.sort(int(config.sort_column), order)

    def sort_indicator_changed(
        self,
        column: int,
        order: Qt.SortOrder,
    ) -> None:
        direction = (
            SortDirection.ASCENDING
            if order == Qt.SortOrder.AscendingOrder
            else SortDirection.DESCENDING
        )

        config = self.get_config()
        self.set_config(
            DirectoryConfig(
                directory=config.directory,
                sort_column=FileColumn(column),
                sort_direction=direction,
            )
        )


@beartype
class DirectoryWindow(QWidget):
    def __init__(self, directory: Path) -> None:
        super().__init__()

        self.setWindowTitle("Configurable directory table")
        self.resize(1000, 650)

        self.table = DirectoryTableView(
            DirectoryConfig(directory=directory),
            parent=self,
        )
        self.config_button = QPushButton("Edit configuration", self)
        self.config_button.clicked.connect(self.edit_config)

        layout = QVBoxLayout(self)
        layout.addWidget(self.config_button)
        layout.addWidget(self.table, 1)

    def edit_config(self, checked: bool) -> None:
        self.table.show_config_dialog()


@beartype
def main() -> int:
    app = QApplication(sys.argv)
    directory = Path(sys.argv[1]) if 1 < len(sys.argv) else Path.cwd()

    window = DirectoryWindow(directory)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
