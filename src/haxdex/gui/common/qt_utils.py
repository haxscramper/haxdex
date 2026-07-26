from __future__ import annotations

from beartype import beartype
from beartype.typing import Any
import pandas as pd

from PyQt6.QtCore import (
    QSettings,
    QAbstractItemModel,
    Qt,
)


def get_settings() -> QSettings:
    return QSettings()


@beartype
def qt_model_to_dataframe(
        model: QAbstractItemModel,
        role: int = int(Qt.ItemDataRole.DisplayRole),
) -> pd.DataFrame:
    row_count = model.rowCount()
    column_count = model.columnCount()

    rows: list[list[Any]] = []
    for row in range(row_count):
        row_values: list[Any] = []
        for column in range(column_count):
            index = model.index(row, column)
            row_values.append(model.data(index, role))

        rows.append(row_values)

    columns = [
        model.headerData(column, Qt.Orientation.Horizontal, role)
        for column in range(column_count)
    ]

    columns = [
        f"column_{column}" if columns[column] is None else columns[column]
        for column in range(column_count)
    ]

    return pd.DataFrame(rows, columns=columns)
