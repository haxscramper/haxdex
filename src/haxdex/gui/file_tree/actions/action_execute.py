from __future__ import annotations

import importlib
import json
from loguru import logger
from datetime import datetime, timezone
from pathlib import Path

from beartype import beartype
from beartype.typing import Any, Optional, Sequence, TypeVar
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from haxdex.gui.file_tree.actions.action_db import OperationRow
from haxdex.gui.file_tree.actions.action_handler import ActionHandler
from haxdex.gui.file_tree.actions.action_list_model import BaseAction, to_action_handler_map
from haxdex.gui.file_tree.actions.action_move_file import MoveActionHandler
from haxdex.gui.file_tree.actions.action_trash_file import TrashActionHandler
from haxdex.gui.file_tree.actions.action_video_convert import VideoConvertActionHandler
from haxdex.services.pydantic_utils import model_to_json_data, model_from_json_data
from haxdex.services.utils import dump_with_type

T = TypeVar("T", bound=BaseModel)

_PENDING_STR = "pending"
_DONE_STR = "done"


class ActionExecutionConfig(BaseModel):
    trash_root: Path
    sqlite_path: Path
    output_directory: Path
    dry_run: bool = True

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


@beartype
def _now() -> datetime:
    return datetime.now(timezone.utc)


@beartype
class ActionExecutor:

    def __init__(self, config: ActionExecutionConfig) -> None:
        self.config = config
        self.engine = create_engine(f"sqlite+pysqlite:///{self.config.sqlite_path}",
                                    future=True)

        self.config.output_directory.mkdir(parents=True, exist_ok=True)
        self.handlers: dict[str, ActionHandler] = to_action_handler_map([
            MoveActionHandler(dry_run=self.config.dry_run,),
            TrashActionHandler(
                trash_root=self.config.trash_root,
                dry_run=self.config.dry_run,
            ),
            VideoConvertActionHandler(
                dry_run=self.config.dry_run,
                output_directory=self.config.output_directory,
            )
        ])

    def init_db(self) -> None:
        from haxdex.gui.file_tree.actions.action_db import Base
        Base.metadata.create_all(self.engine)

    def verify_actions_consistency(self, actions: Sequence[BaseAction]) -> None:
        move_counts: dict[Path, int] = {}
        convert_counts: dict[Path, int] = {}
        trash_paths: set[Path] = set()

        for action in actions:
            kind = action.kind
            if kind not in self.handlers:
                raise ValueError(f"Unsupported action kind: {kind}")

            self.handlers[kind].verify_consistency_single(action)
            src = action.file.path

            match kind:
                case "move":
                    move_counts[src] = move_counts.get(src, 0) + 1
                case "trash":
                    trash_paths.add(src)
                case "video_convert":
                    convert_counts[src] = convert_counts.get(src, 0) + 1
                case _:
                    raise ValueError(f"Unsupported action kind: {kind}")

        for src, count in move_counts.items():
            if 1 < count:
                raise ValueError(
                    f"Multiple move actions from the same source path: {src}")
            if src in trash_paths:
                raise ValueError(
                    f"Conflicting move and trash action for source path: {src}")
            if src in convert_counts:
                raise ValueError(
                    f"Conflicting move and video_convert action for source path: {src}")

        for src, count in convert_counts.items():
            if 1 < count:
                raise ValueError(
                    f"Multiple video_convert actions from the same source path: {src}")
            if src in trash_paths:
                raise ValueError(
                    f"Conflicting video_convert and trash action for source path: {src}")

    def register_actions(self, actions: Sequence[BaseAction]) -> None:
        self.verify_actions_consistency(actions)

        # Deduplicate incoming actions by hash and ensure type consistency in-batch.
        incoming_by_hash: dict[str, tuple[BaseAction, str, str]] = {}
        for action in actions:
            kind = action.kind
            execution_hash = self.handlers[kind].get_hash(action)

            existing_in_batch = incoming_by_hash.get(execution_hash)
            if existing_in_batch is not None:
                _, existing_kind, _ = existing_in_batch
                if existing_kind != action.kind:
                    raise ValueError(
                        f"Conflicting action types for hash '{execution_hash}': "
                        f"'{existing_kind}' vs '{action.kind}'")
                continue

            incoming_by_hash[execution_hash] = (action, action.kind, kind)

        if not incoming_by_hash:
            return

        with Session(self.engine) as session:
            existing_rows = session.execute(
                select(OperationRow.execution_hash, OperationRow.kind).where(
                    OperationRow.execution_hash.in_(list(
                        incoming_by_hash.keys())))).all()
            existing_by_hash = {row.execution_hash: row.kind for row in existing_rows}

            for execution_hash, existing_kind in existing_by_hash.items():
                _, incoming_kind, _ = incoming_by_hash[execution_hash]
                if existing_kind != incoming_kind:
                    raise ValueError(
                        f"Hash '{execution_hash}' already exists with kind '{existing_kind}', "
                        f"cannot register action of kind '{incoming_kind}'")

            for execution_hash, (action, action_kind, kind) in incoming_by_hash.items():
                if execution_hash in existing_by_hash:
                    continue

                session.add(
                    OperationRow(
                        kind=kind,
                        action_data=model_to_json_data(action),
                        status=_PENDING_STR,
                        execution_hash=execution_hash,
                        started_at=None,
                        finished_at=None,
                    ))

            session.commit()

    def _load_action(self, row: OperationRow) -> BaseAction:
        result = model_from_json_data(row.action_data,
                                      self.handlers[row.kind].action_type)
        assert (isinstance(result, BaseAction) and type(result) is not BaseAction and
                type(result) is not BaseModel), "type {}\n{}".format(
                    type(result), json.dumps(dump_with_type(result), indent=2))

        return result

    def load_all_actions(self) -> list[BaseAction]:
        result = list()
        with Session(self.engine) as session:
            for row in session.scalars(select(OperationRow)):
                result.append(self._load_action(row))

        return result

    def execute_pending(self, max_operations: Optional[int] = None) -> int:
        self.config.trash_root.mkdir(parents=True, exist_ok=True)
        executed = 0
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(OperationRow).where(
                        OperationRow.status == _PENDING_STR).order_by(
                            OperationRow.id.asc())))
            for row in rows:
                if max_operations is not None and max_operations <= executed:
                    break
                action = self._load_action(row)
                row.started_at = _now()
                session.commit()
                self.handlers[row.kind].do_action(row, action)
                row.status = _DONE_STR
                row.finished_at = _now()
                session.commit()
                executed += 1
        return executed

    def revert_done(self) -> int:
        reverted = 0
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(OperationRow).where(OperationRow.status == _DONE_STR).order_by(
                        OperationRow.id.desc())))
            for row in rows:
                action = self._load_action(row)
                self.handlers[row.kind].undo_action(row, action)
                row.status = _PENDING_STR
                row.started_at = None
                row.finished_at = None
                session.commit()
                reverted += 1
        return reverted
