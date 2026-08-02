import hashlib
import shutil
from pathlib import Path
from typing import ClassVar

from beartype import beartype

from haxdex.gui.file_tree.actions.action_db import OperationRow
from haxdex.gui.file_tree.actions.action_handler import ActionHandler, BaseAction

from loguru import logger
import os


class TrashAction(BaseAction):
    kind: ClassVar[str] = "trash"


def validate_output_path(dest: Path) -> None:
    parent = dest.parent

    if not parent.exists():
        raise FileNotFoundError(parent)

    if not parent.is_dir():
        raise NotADirectoryError(parent)

    fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    os.unlink(dest)


@beartype
class TrashActionHandler(ActionHandler):
    action_type = TrashAction

    def __init__(self, trash_root: Path, dry_run: bool) -> None:
        self.trash_root = trash_root
        self.dry_run = dry_run

    def _get_base_dest(self, action: TrashAction) -> Path:
        return self.trash_root / action.file.root / action.file.root_relative

    def get_dest_forward(self, action: TrashAction) -> Path:
        base = self._get_base_dest(action)
        base.parent.mkdir(parents=True, exist_ok=True)

        if not base.exists():
            return base

        idx = 1
        while True:
            candidate = base.with_name(f"{base.name}.{idx}")
            if not candidate.exists():
                return candidate
            idx += 1

    def get_dest_undo(self, action: TrashAction) -> Path:
        base = self._get_base_dest(action)
        parent = base.parent

        if not parent.exists():
            raise FileNotFoundError(f"No trash entry found for {action.file.path}")

        prefix = f"{base.name}."
        best_idx = -1
        best_path: Path | None = None

        if base.exists():
            best_idx = 0
            best_path = base

        for p in parent.iterdir():
            if not p.name.startswith(prefix):
                continue
            suffix = p.name[len(prefix):]
            if not suffix.isdigit():
                continue
            idx = int(suffix)
            if idx > best_idx:
                best_idx = idx
                best_path = p

        if best_path is None:
            raise FileNotFoundError(f"No trash entry found for {action.file.path}")

        return best_path

    def do_action(self, row: OperationRow, action: BaseAction) -> None:
        assert isinstance(action, TrashAction)
        src = Path(action.file.path).absolute()
        dest = self.get_dest_forward(action).absolute()

        logger.debug(f"do trash: executing move('{src}' -> '{dest}')")

        if self.dry_run:
            return

        validate_output_path(dest)
        shutil.move(str(src), str(dest))

    def undo_action(self, row: OperationRow, action: BaseAction) -> None:
        assert isinstance(action, TrashAction)
        restore_dest = Path(action.file.path).absolute()
        trash_src = self.get_dest_undo(action).absolute()

        logger.debug(f"undo trash: executing move('{trash_src}' -> '{restore_dest}')")

        if self.dry_run:
            return

        restore_dest.parent.mkdir(parents=True, exist_ok=True)
        validate_output_path(restore_dest)
        shutil.move(str(trash_src), str(restore_dest))

    def get_hash(self, action: BaseAction) -> str:
        assert isinstance(action, TrashAction)
        src = action.file.path
        payload = f"trash|{src}|{self.trash_root}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify_consistency_single(self, action: BaseAction) -> None:
        assert isinstance(action, TrashAction)
        src = Path(action.file.path).absolute()

        if src == self.trash_root.absolute():
            raise ValueError(f"Trash source cannot be trash root: {src}")

        do_dest = self.get_dest_forward(action).absolute()
        validate_output_path(do_dest)
