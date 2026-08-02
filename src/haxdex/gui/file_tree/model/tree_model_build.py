from __future__ import annotations

import json
from loguru import logger
from collections.abc import Sequence
from pathlib import Path
from pprint import pformat
from typing import Optional

from beartype import beartype
from pydantic import BaseModel
from sqlalchemy import (
    Table,
    select,
)
from sqlalchemy.engine import Engine

from haxdex.cli.cli_config import DirConfig
from haxdex.gui.file_tree.columns.file_tree_column import (
    FileTreeColumnSpec,
    FileTreeInitArgs,
    FileTreeNode,
)
from haxdex.gui.file_tree.model.tree_model_cache import initialize_cache, populate_cache
from haxdex.gui.file_tree.model.tree_model_fetch import fetch_file_paths
from haxdex.gui.file_tree.model.tree_model_user_edits import load_user_edits, apply_user_edits
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_types import BaseIndexer, RunContext
from haxdex.services.core.types import FileHash
from haxdex.services.file_iteration import match_root, RootFilter, prepare_root_filters
from haxdex.services.pydantic_utils import model_from_json_data, model_to_json_data


def _load_flat_file_nodes(
    engine: Engine,
    file_table: Table,
    path_table: Table,
    root_filters: Sequence[RootFilter],
    columns: Sequence[FileTreeColumnSpec],
) -> list[tuple[Path, FileTreeNode]]:
    query = (select(
        path_table.c.path,
        path_table.c.hash,
        path_table.c.root,
        path_table.c.relative,
        *[file_table.c[column.column_name] for column in columns],
    ).select_from(
        path_table.join(
            file_table,
            (path_table.c.hash == file_table.c.hash) &
            (path_table.c.root == file_table.c.root) &
            (path_table.c.relative == file_table.c.relative),
        )).order_by(path_table.c.path))

    flat_nodes: list[tuple[Path, FileTreeNode]] = []

    with engine.connect() as connection:
        for row in connection.execute(query):
            row_data = row._mapping
            path_str = row_data["path"]

            matched = match_root(path_str, root_filters)
            if matched is None:
                continue

            root_filter, _ = matched

            path = Path(path_str)
            file_hash = FileHash(hash=row_data["hash"])
            node_columns: dict[str, Optional[BaseModel]] = {}

            for column in columns:
                json_data = json.loads(row_data[column.column_name])
                node_columns[column.column_name] = (None if json_data is None else
                                                    model_from_json_data(
                                                        json_data, column.column_type))

            if "file_name" in node_columns:
                fn = node_columns["file_name"]
                assert str(path).endswith(fn.name), f"{path} --> {fn.name}"

            flat_nodes.append((
                root_filter.root_path,
                FileTreeNode(
                    path=path,
                    is_directory=False,
                    hash=file_hash,
                    columns=node_columns,
                    root_relative=row_data["relative"],
                    root=row_data["root"],
                ),
            ))

    return flat_nodes


def _build_directory_tree(
    flat_nodes: Sequence[tuple[Path, FileTreeNode]],
    columns: Sequence[FileTreeColumnSpec],
) -> list[FileTreeNode]:
    files_by_parent: dict[tuple[Path, Path], list[FileTreeNode]] = {}
    child_dirs_by_parent: dict[tuple[Path, Path], list[Path]] = {}
    child_dir_sets: dict[tuple[Path, Path], set[Path]] = {}
    root_names: dict[Path, str] = {}
    roots: set[Path] = set()

    for root_path, file_node in flat_nodes:
        roots.add(root_path)

        if file_node.root is not None:
            root_names.setdefault(root_path, file_node.root)

        relative_parts = file_node.path.relative_to(root_path).parts
        parent_path = root_path

        for part in relative_parts[:-1]:
            child_path = parent_path / part
            key = (root_path, parent_path)

            seen_children = child_dir_sets.setdefault(key, set())
            if child_path not in seen_children:
                seen_children.add(child_path)
                child_dirs_by_parent.setdefault(key, []).append(child_path)

            parent_path = child_path

        files_by_parent.setdefault((root_path, parent_path), []).append(file_node)

    for key in child_dirs_by_parent:
        child_dirs_by_parent[key].sort()

    for key in files_by_parent:
        files_by_parent[key].sort(key=lambda node: node.path)

    def _root_relative(directory_path: Path, root_path: Path) -> str:
        if directory_path == root_path:
            return ""
        return directory_path.relative_to(root_path).as_posix()

    def build_directory(root_path: Path, directory_path: Path) -> FileTreeNode:
        key = (root_path, directory_path)

        nested: list[FileTreeNode] = []
        for child_path in child_dirs_by_parent.get(key, []):
            nested.append(build_directory(root_path, child_path))

        nested.extend(files_by_parent.get(key, []))

        col_data = {
            column.column_name:
                column.initColumnData(
                    args=FileTreeInitArgs(
                        path=directory_path,
                        hash=None,
                        is_directory=True,
                        root=root_names.get(root_path),
                        relative=_root_relative(directory_path, root_path),
                    ),
                    assets={},
                    nested=nested,
                ) for column in columns
        }

        return FileTreeNode(
            path=directory_path,
            is_directory=True,
            hash=None,
            root=root_names.get(root_path),
            root_relative=_root_relative(directory_path, root_path),
            columns=col_data,
            nested=nested,
        )

    return [build_directory(root_path, root_path) for root_path in sorted(roots)]


def load_file_tree_from_cache(
    ctx: RunContext,
    engine: Engine,
    file_table: Table,
    path_table: Table,
    root_filters: Sequence[RootFilter],
    columns: Sequence[FileTreeColumnSpec],
) -> list[FileTreeNode]:
    with ctx.trace_scope("load file tree cache"):
        flat_nodes = _load_flat_file_nodes(
            engine,
            file_table,
            path_table,
            root_filters,
            columns,
        )

        logger.debug(f"loaded flat file nodes {len(flat_nodes)} entries")

    with ctx.trace_scope("arrange file tree"):
        return _build_directory_tree(flat_nodes, columns)


@beartype
def build_file_tree(
    ctx: RunContext,
    db: IndexDatabase,
    root_directories: Sequence[DirConfig],
    indexers: Sequence[BaseIndexer],
    columns: Sequence[FileTreeColumnSpec],
    cache_path: Path,
    user_edit_path: Path,
) -> list[FileTreeNode]:
    assert 0 < len(root_directories)
    root_filters = prepare_root_filters(root_directories)
    if not root_filters:
        logger.warning("no root filters")
        return []

    file_paths = fetch_file_paths(ctx, db, root_filters)

    assert 0 < len(file_paths), "\n{}".format(pformat(root_filters))

    engine, _, file_table, path_table = initialize_cache(
        cache_path,
        columns,
    )

    try:
        populate_cache(
            ctx,
            db,
            engine,
            file_table,
            path_table,
            file_paths,
            indexers,
            columns,
        )

        nodes = load_file_tree_from_cache(
            ctx,
            engine,
            file_table,
            path_table,
            root_filters,
            columns,
        )

        Path("/tmp/nodes.json").write_text(
            json.dumps([model_to_json_data(n) for n in nodes], indent=2))

        assert 0 < len(nodes), pformat(root_directories)
        user_edit_rows = load_user_edits(user_edit_path, columns)
        apply_user_edits(nodes, columns, user_edit_rows)
        assert 0 < len(nodes)
        return nodes
    finally:
        engine.dispose()
