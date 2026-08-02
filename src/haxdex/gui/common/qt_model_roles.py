import enum

from PyQt6.QtCore import Qt


class CustomModelRole(enum.Enum):
    HashRole = Qt.ItemDataRole.UserRole + 1
    PathRole = Qt.ItemDataRole.UserRole + 2
    ExtraRole = Qt.ItemDataRole.UserRole + 3
    ActionRole = Qt.ItemDataRole.UserRole + 4
    FullDataRole = Qt.ItemDataRole.UserRole + 5
    ColumnSpecRole = Qt.ItemDataRole.UserRole + 6
    SortDataRole = Qt.ItemDataRole.UserRole + 7
    FilterDataRole = Qt.ItemDataRole.UserRole + 8
