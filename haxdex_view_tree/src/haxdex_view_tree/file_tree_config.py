from pathlib import Path

from beartype.typing import Optional
from haxdex_core.file_iteration import DirConfig
from hstd_py_lib.pydantic_utils import OutputFile
from pydantic import BaseModel


class FileTreeViewConfig(BaseModel, extra="forbid"):
    root_dirs: list[DirConfig]
    reference_dir: Optional[DirConfig] = None
    reference_tree_cache_path: OutputFile = Path("/tmp/reference_tree_cache.sqlite")
    visual_tree_cache_path: OutputFile = Path("/tmp/input_tree_cache.sqlite")
    drop_cache_files: bool = False
    user_edit_path: OutputFile = Path("~/tmp/user_actions.sqlite").expanduser()
