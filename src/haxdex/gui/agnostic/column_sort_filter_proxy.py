from beartype import beartype
from beartype.typing import Any, Callable, Sequence

from PyQt6.QtCore import QModelIndex, Qt, QSortFilterProxyModel

from haxdex.gui.common.qt_model_roles import CustomModelRole


@beartype
class ColumnSortFilterProxyModel(QSortFilterProxyModel):

    def __init__(self, parent: None | object = None) -> None:
        super().__init__(parent)
        self.sortPriority: tuple[int, ...] = tuple()
        self.activeSortPriority: tuple[int, ...] = tuple()
        self.sortRules: dict[int, Callable[[Any, Any], int]] = {}
        self.filterRules: dict[int, Callable[[Any], bool]] = {}
        self.sortRoles: dict[int, int] = {}
        self.filterRoles: dict[int, int] = {}
        self.defaultSortRole: int = CustomModelRole.SortDataRole.value
        self.defaultFilterRole: int = CustomModelRole.FilterDataRole.value

    def setSortPriority(self, columns: Sequence[int]) -> None:
        priority = tuple(columns)
        if len(set(priority)) != len(priority):
            duplicate = next(column for column in priority if 1 < priority.count(column))
            raise ValueError(f"Sort priority contains duplicate column index {duplicate}")

        model = self.sourceModel()
        if model is not None:
            columnCount = model.columnCount(QModelIndex())
            for column in priority:
                if column < 0 or columnCount <= column:
                    raise ValueError(
                        f"Sort priority column {column} is out of range for model with {columnCount} columns"
                    )

        self.sortPriority = priority
        self.invalidate()
        self.sort(0, self.sortOrder())

    def setSortRule(self, column: int, rule: None | Callable[[Any, Any], int]) -> None:
        match rule:
            case None:
                self.sortRules.pop(column, None)
            case _:
                self.sortRules[column] = rule
        self.invalidate()
        self.sort(0, self.sortOrder())

    def setFilterRule(self, column: int, rule: None | Callable[[Any], bool]) -> None:
        match rule:
            case None:
                self.filterRules.pop(column, None)
            case _:
                self.filterRules[column] = rule
        self.invalidateFilter()

    def setSortRoleForColumn(self, column: int, role: int) -> None:
        self.sortRoles[column] = role
        self.invalidate()
        self.sort(0, self.sortOrder())

    def setFilterRoleForColumn(self, column: int, role: int) -> None:
        self.filterRoles[column] = role
        self.invalidateFilter()

    def sort(self,
             column: int,
             order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        model = self.sourceModel()
        if model is None:
            super().sort(column, order)
            return

        columnCount = model.columnCount(QModelIndex())
        if columnCount == 0:
            super().sort(column, order)
            return

        fixedColumn = column
        if fixedColumn < 0 or columnCount <= fixedColumn:
            fixedColumn = 0

        match len(self.sortPriority):
            case 0:
                self.activeSortPriority = tuple(
                    [fixedColumn] +
                    [idx for idx in range(columnCount) if idx != fixedColumn])
            case _:
                self.activeSortPriority = self.sortPriority

        super().sort(self.activeSortPriority[0], order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return left.row() < right.row()

        columnCount = model.columnCount(QModelIndex())
        match len(self.activeSortPriority):
            case 0:
                priority = tuple(range(columnCount))
            case _:
                priority = self.activeSortPriority

        for column in priority:
            leftIndex = model.index(left.row(), column, left.parent())
            rightIndex = model.index(right.row(), column, right.parent())
            role = self.sortRoles.get(column, self.defaultSortRole)
            leftValue = model.data(leftIndex, role)
            rightValue = model.data(rightIndex, role)
            compare = self.compareColumnValues(column, leftValue, rightValue)

            if compare < 0:
                return True
            if compare == 0:
                continue
            return False

        return left.row() < right.row()

    def filterAcceptsRow(self, sourceRow: int, sourceParent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True

        for column, rule in self.filterRules.items():
            index = model.index(sourceRow, column, sourceParent)
            role = self.filterRoles.get(column, self.defaultFilterRole)
            value = model.data(index, role)
            if not rule(value):
                return False

        return True

    def compareColumnValues(self, column: int, leftValue: Any, rightValue: Any) -> int:
        rule = self.sortRules.get(column)
        if rule is not None:
            return rule(leftValue, rightValue)

        match (leftValue is None, rightValue is None):
            case (True, True):
                return 0
            case (True, False):
                return -1
            case (False, True):
                return 1
            case _:
                pass

        try:
            if leftValue < rightValue:
                return -1
            if rightValue < leftValue:
                return 1
            return 0
        except TypeError:
            leftText = str(leftValue)
            rightText = str(rightValue)
            if leftText < rightText:
                return -1
            if rightText < leftText:
                return 1
            return 0
