from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from haxdex.gui.file_tree.actions.action_db import OperationRow
from haxdex.gui.file_tree.actions.action_execute import (
    ActionExecutionConfig,
    ActionExecutor,
)
from haxdex.gui.file_tree.actions.action_list_model import Action, MoveAction, TrashAction
from haxdex.gui.file_tree.columns.file_tree_column import FileTreeNode


@pytest.fixture
def action_executor(stable_test_dir: Path) -> ActionExecutor:
    config = ActionExecutionConfig(
        trash_root=stable_test_dir / "trash",
        sqlite_path=stable_test_dir / "ops.sqlite",
        dry_run=False,
        output_directory=stable_test_dir / "result",
    )
    executor = ActionExecutor(config=config)
    executor.init_db()
    return executor


def test_execute_move_and_trash(action_executor: ActionExecutor,
                                stable_test_dir: Path) -> None:
    source_move = stable_test_dir / "a.txt"
    source_trash = stable_test_dir / "b.txt"
    move_dest = stable_test_dir / "moved" / "a.txt"

    source_move.write_text("move", encoding="utf-8")
    source_trash.write_text("trash", encoding="utf-8")
    move_dest.parent.mkdir(parents=True, exist_ok=True)

    actions: list[Action] = [
        MoveAction(file=FileTreeNode(path=source_move, is_directory=False),
                   dest=str(move_dest)),
        TrashAction(file=FileTreeNode(path=source_trash, is_directory=False)),
    ]

    action_executor.register_actions(actions=actions)
    executed = action_executor.execute_pending()
    assert executed == 2
    assert move_dest.exists()
    assert source_move.exists() is False
    assert source_trash.exists() is False

    with Session(action_executor.engine) as session:
        rows = list(session.scalars(select(OperationRow).order_by(OperationRow.id.asc())))
        assert len(rows) == 2
        assert rows[0].status == "done"
        assert rows[1].status == "done"
        assert rows[0].started_at is not None
        assert rows[0].finished_at is not None
        assert rows[1].started_at is not None
        assert rows[1].finished_at is not None
        assert rows[0].action_type
        assert rows[1].action_type
        assert rows[0].action_data is not None
        assert rows[1].action_data is not None

        trash_file = action_executor.config.trash_root / f"{rows[1].id}_{source_trash.name}"
        assert trash_file.exists()


def test_resume_execution(action_executor: ActionExecutor, stable_test_dir: Path) -> None:
    source_move = stable_test_dir / "resume_a.txt"
    source_trash = stable_test_dir / "resume_b.txt"
    move_dest = stable_test_dir / "resume_moved" / "resume_a.txt"

    source_move.write_text("move", encoding="utf-8")
    source_trash.write_text("trash", encoding="utf-8")
    move_dest.parent.mkdir(parents=True, exist_ok=True)

    actions: list[Action] = [
        MoveAction(file=FileTreeNode(path=source_move, is_directory=False),
                   dest=str(move_dest)),
        TrashAction(file=FileTreeNode(path=source_trash, is_directory=False)),
    ]

    action_executor.register_actions(actions=actions)
    first_executed = action_executor.execute_pending(max_operations=1)
    assert first_executed == 1

    with Session(action_executor.engine) as session:
        rows = list(session.scalars(select(OperationRow).order_by(OperationRow.id.asc())))
        assert rows[0].status == "done"
        assert rows[1].status == "pending"

    resumed = action_executor.execute_pending()
    assert resumed == 1
    assert move_dest.exists()
    assert source_move.exists() is False
    assert source_trash.exists() is False


def test_revert_done(action_executor: ActionExecutor, stable_test_dir: Path) -> None:
    source_move = stable_test_dir / "rev_a.txt"
    source_trash = stable_test_dir / "rev_b.txt"
    move_dest = stable_test_dir / "rev_moved" / "rev_a.txt"

    source_move.write_text("move", encoding="utf-8")
    source_trash.write_text("trash", encoding="utf-8")
    move_dest.parent.mkdir(parents=True, exist_ok=True)

    actions: list[Action] = [
        MoveAction(file=FileTreeNode(path=source_move, is_directory=False),
                   dest=str(move_dest)),
        TrashAction(file=FileTreeNode(path=source_trash, is_directory=False)),
    ]

    action_executor.register_actions(actions=actions)
    action_executor.execute_pending()

    reverted = action_executor.revert_done()
    assert reverted == 2
    assert source_move.exists()
    assert source_trash.exists()
    assert move_dest.exists() is False

    with Session(action_executor.engine) as session:
        rows = list(session.scalars(select(OperationRow).order_by(OperationRow.id.asc())))
        assert rows[0].action_data is not None
        assert rows[1].action_data is not None

        trash_file = action_executor.config.trash_root / f"{rows[1].id}_{source_trash.name}"
        assert trash_file.exists() is False


def test_register_actions_skips_duplicate_same_type(
    action_executor: ActionExecutor,
    stable_test_dir: Path,
) -> None:
    source = stable_test_dir / "dup_a.txt"
    move_dest = stable_test_dir / "dup_moved" / "dup_a.txt"

    source.write_text("move", encoding="utf-8")
    move_dest.parent.mkdir(parents=True, exist_ok=True)

    action = MoveAction(
        file=FileTreeNode(path=source, is_directory=False),
        dest=str(move_dest),
    )

    action_executor.register_actions(actions=[action])
    action_executor.register_actions(actions=[action])

    with Session(action_executor.engine) as session:
        rows = list(session.scalars(select(OperationRow).order_by(OperationRow.id.asc())))
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].execution_hash

    executed = action_executor.execute_pending()
    assert executed == 1
    assert move_dest.exists()
    assert source.exists() is False
