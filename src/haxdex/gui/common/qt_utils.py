from __future__ import annotations

from beartype import beartype
from beartype.typing import Any, Mapping, Sequence
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
    role: int | Sequence[int] = int(Qt.ItemDataRole.DisplayRole),
    role_names: Mapping[int, str] | None = None,
) -> pd.DataFrame:
    if isinstance(role, int):
        roles = [role]
        multi_role = False
    else:
        roles = [int(r) for r in role]
        if len(roles) == 0:
            raise ValueError("`role` sequence must not be empty")
        multi_role = True

    resolved_role_names: dict[int, str] = {}

    model_role_names = model.roleNames()
    for role_id, role_name in model_role_names.items():
        if hasattr(role_name, "data"):
            resolved_role_names[int(role_id)] = bytes(role_name.data()).decode()
        else:
            resolved_role_names[int(role_id)] = bytes(role_name).decode()

    if role_names is not None:
        resolved_role_names.update({int(k): v for k, v in role_names.items()})

    unresolved = [r for r in roles if r not in resolved_role_names]
    if unresolved:
        user_role_base = int(Qt.ItemDataRole.UserRole)

        def format_role(role_value: int) -> str:
            if role_value >= user_role_base:
                return f"UserRole+{role_value - user_role_base}"
            return str(role_value)

        unresolved_display = [format_role(r) for r in unresolved]
        raise ValueError(
            f"Could not resolve role names for roles: {unresolved_display}. "
            "Provide them via `role_names` or ensure `model.roleNames()` contains them.")

    row_count = model.rowCount()
    column_count = model.columnCount()

    rows: list[list[Any]] = []
    for row in range(row_count):
        row_values: list[Any] = []
        for column in range(column_count):
            index = model.index(row, column)
            if multi_role:
                cell_value = {resolved_role_names[r]: model.data(index, r) for r in roles}
            else:
                cell_value = model.data(index, roles[0])
            row_values.append(cell_value)
        rows.append(row_values)

    columns = [
        model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        for column in range(column_count)
    ]
    columns = [
        f"column_{column}" if columns[column] is None else columns[column]
        for column in range(column_count)
    ]

    return pd.DataFrame(rows, columns=columns)
