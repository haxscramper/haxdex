from __future__ import annotations

from PyQt6.QtCore import (
    QSettings,)


def get_settings() -> QSettings:
    return QSettings()
