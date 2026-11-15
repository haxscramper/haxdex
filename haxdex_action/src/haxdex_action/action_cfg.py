from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, field_validator


class ActionExecutionConfig(BaseModel, extra="forbid", frozen=True):
    trash_root: Path
    sqlite_path: Path
    output_directory: Path
    dry_run: bool = True
    max_actions: int | None = None

    @field_validator("output_directory")
    @classmethod
    def _normalize_output_directory_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve().absolute()

    @field_validator("trash_root")
    @classmethod
    def _normalize_trash_root_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve().absolute()

    @field_validator("sqlite_path")
    @classmethod
    def _normalize_sqlite_path_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve().absolute()


class ActionConfig(BaseModel, extra="forbid", frozen=True):
    execution: ActionExecutionConfig
