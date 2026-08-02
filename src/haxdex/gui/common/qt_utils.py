from __future__ import annotations

from beartype import beartype
from beartype.typing import Any, Mapping, Sequence
import pandas as pd

from PyQt6.QtCore import (
    QModelIndex,
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
    with_tree_path: bool = False,
    with_tree_depth: bool = False,
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

    root_column_count = model.columnCount()
    column_names = [
        model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        for column in range(root_column_count)
    ]
    column_names = [
        f"column_{column}" if column_names[column] is None else column_names[column]
        for column in range(root_column_count)
    ]

    rows: list[dict[str, Any]] = []

    def collect_rows(parent: QModelIndex, path: tuple[int, ...]) -> None:
        row_count = model.rowCount(parent)
        column_count = model.columnCount(parent)

        for row in range(row_count):
            row_dict: dict[str, Any] = {}

            if with_tree_depth:
                row_dict["_tree_depth"] = len(path)

            if with_tree_path:
                row_dict["_tree_path"] = path + (row,),

            for column in range(column_count):
                index = model.index(row, column, parent)
                column_name = (column_names[column]
                               if column < len(column_names) else f"column_{column}")
                if multi_role:
                    row_dict[column_name] = {
                        resolved_role_names[r]: model.data(index, r) for r in roles
                    }
                else:
                    row_dict[column_name] = model.data(index, roles[0])

            rows.append(row_dict)
            collect_rows(model.index(row, 0, parent), path + (row,))

    collect_rows(QModelIndex(), ())

    return pd.DataFrame(rows)
