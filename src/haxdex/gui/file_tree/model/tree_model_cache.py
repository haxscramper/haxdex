from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    bindparam,)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from haxdex.gui.file_tree.model.tree_model_fetch import _FilePathRow, fetch_indexer_assets
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_types import BaseIndexer, RunContext
from haxdex.services.core.types import FileHash
from haxdex.services.pydantic_utils import model_to_json_data
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import (
    Column,
    MetaData,
    Table,
    Text,
    delete,
    inspect,
    select,
)
from sqlalchemy.engine import Engine

from haxdex.gui.file_tree.columns.file_tree_column import (
    FileTreeColumnSpec,
    FileTreeInitArgs,
)
from haxdex.services.utils import create_cache_engine
from loguru import logger

CACHE_SCHEMA_TABLE = "file_tree_column_schemas"
CACHE_FILE_TABLE = "file_tree_file_columns"
CACHE_PATH_TABLE = "file_tree_paths"


@dataclass(slots=True, frozen=True)
class _CachePathKey:
    hash: str
    root: str
    relative: str


def _row_cache_key(row: _FilePathRow) -> _CachePathKey:
    return _CachePathKey(hash=row.hash, root=row.root, relative=row.relative)


def _column_schema_hash(column: FileTreeColumnSpec) -> str:
    schema = column.column_type.model_json_schema()

    encoded_schema = json.dumps(
        schema,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    return hashlib.sha256(encoded_schema).hexdigest()


def _validate_columns(columns: Sequence[FileTreeColumnSpec]) -> None:
    names = [column.column_name for column in columns]

    duplicates = [name for name in dict.fromkeys(names) if names.count(name) > 1]
    if duplicates:
        raise ValueError(
            f"File tree column names must be unique; duplicate items: {duplicates}")

    reserved_names = {"hash", "root", "relative"}
    reserved_in_use = sorted(name for name in names if name in reserved_names)
    if reserved_in_use:
        raise ValueError(
            f"Reserved cache column names cannot be used as file tree columns: {reserved_in_use}",
        )


def _build_cache_tables(
    columns: Sequence[FileTreeColumnSpec],) -> tuple[MetaData, Table, Table, Table]:
    metadata = MetaData()

    schema_table = Table(
        CACHE_SCHEMA_TABLE,
        metadata,
        Column("column_name", Text, primary_key=True),
        Column("schema_hash", Text, nullable=False),
    )

    file_table = Table(
        CACHE_FILE_TABLE,
        metadata,
        Column("hash", Text, primary_key=True),
        Column("root", Text, primary_key=True),
        Column("relative", Text, primary_key=True),
        *[Column(column.column_name, Text, nullable=False) for column in columns],
    )

    path_table = Table(
        CACHE_PATH_TABLE,
        metadata,
        Column("path", Text, primary_key=True),
        Column("hash", Text, nullable=False),
        Column("root", Text, nullable=False),
        Column("relative", Text, nullable=False),
    )

    return metadata, schema_table, file_table, path_table


def _drop_cache_database(engine: Engine) -> None:
    existing_metadata = MetaData()
    existing_metadata.reflect(bind=engine)
    existing_metadata.drop_all(bind=engine)


def initialize_cache(
    cache_path: Path,
    columns: Sequence[FileTreeColumnSpec],
) -> tuple[Engine, Table, Table, Table]:
    _validate_columns(columns)

    engine = create_cache_engine(cache_path)
    metadata, schema_table, file_table, path_table = _build_cache_tables(columns)

    expected_schema_hashes = {
        column.column_name: _column_schema_hash(column) for column in columns
    }

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    required_tables = {
        CACHE_SCHEMA_TABLE,
        CACHE_FILE_TABLE,
        CACHE_PATH_TABLE,
    }

    cache_needs_rebuild = not required_tables.issubset(existing_tables)

    if not required_tables.issubset(existing_tables):
        logger.info(
            f"Existing DB tables {existing_tables} is not a subset of requried tables {required_tables}"
        )

    if not cache_needs_rebuild:
        path_table_columns = {
            column_info["name"] for column_info in inspector.get_columns(CACHE_PATH_TABLE)
        }
        expected_path_table_columns = {"path", "hash", "root", "relative"}

        cache_needs_rebuild = (len(path_table_columns) != len(expected_path_table_columns)
                               or path_table_columns != expected_path_table_columns)

        if len(path_table_columns) != len(expected_path_table_columns):
            logger.info(
                f"Expected path table columns len({expected_path_table_columns}) != path table columns len({path_table_columns})"
            )

        if path_table_columns != expected_path_table_columns:
            logger.info(
                f"Expected path table columns {expected_path_table_columns} != path table columns {path_table_columns}"
            )

    if not cache_needs_rebuild:
        file_table_columns = {
            column_info["name"] for column_info in inspector.get_columns(CACHE_FILE_TABLE)
        }
        expected_file_table_columns = {
            "hash",
            "root",
            "relative",
            *[column.column_name for column in columns],
        }

        cache_needs_rebuild = (len(file_table_columns) != len(expected_file_table_columns)
                               or file_table_columns != expected_file_table_columns)

        if len(file_table_columns) != len(expected_file_table_columns):
            logger.info(
                f"Expected file table columns len({expected_file_table_columns}) != file table columns len({file_table_columns})"
            )

        if file_table_columns != expected_file_table_columns:
            logger.info(
                f"Expected file table columns {expected_file_table_columns} != file table columns {file_table_columns}"
            )

    if cache_needs_rebuild:
        logger.info("Need to rebuild cache")
        _drop_cache_database(engine)
        metadata.create_all(engine)

        with engine.begin() as connection:
            connection.execute(
                schema_table.insert(),
                [{
                    "column_name": column_name,
                    "schema_hash": schema_hash,
                } for column_name, schema_hash in expected_schema_hashes.items()],
            )

        return engine, schema_table, file_table, path_table

    with engine.connect() as connection:
        cached_schema_hashes = dict(
            connection.execute(
                select(
                    schema_table.c.column_name,
                    schema_table.c.schema_hash,
                ),).all(),)

    schema_changed = cached_schema_hashes != expected_schema_hashes

    if schema_changed:
        logger.info("Schema changed, dropping tables")
        file_table.drop(engine)
        file_table.create(engine)

        with engine.begin() as connection:
            connection.execute(delete(schema_table))
            connection.execute(
                schema_table.insert(),
                [{
                    "column_name": column_name,
                    "schema_hash": schema_hash,
                } for column_name, schema_hash in expected_schema_hashes.items()],
            )

    logger.info("OK")
    return engine, schema_table, file_table, path_table


def _get_uncached_rows(
    engine: Engine,
    file_table: Table,
    file_paths: Sequence[_FilePathRow],
) -> list[_FilePathRow]:
    ordered_rows_by_key: dict[_CachePathKey, _FilePathRow] = {}

    for row in file_paths:
        key = _row_cache_key(row)
        if key not in ordered_rows_by_key:
            ordered_rows_by_key[key] = row

    with engine.connect() as connection:
        cached_keys = {
            _CachePathKey(hash=row.hash, root=row.root, relative=row.relative)
            for row in connection.execute(
                select(
                    file_table.c.hash,
                    file_table.c.root,
                    file_table.c.relative,
                ),)
        }

    return [row for key, row in ordered_rows_by_key.items() if key not in cached_keys]


def _store_path_hashes(
    engine: Engine,
    path_table: Table,
    file_paths: Sequence[_FilePathRow],
) -> None:
    path_rows = {result.path: result for result in file_paths}

    path_insert = sqlite_insert(path_table)
    path_upsert = path_insert.on_conflict_do_update(
        index_elements=[path_table.c.path],
        set_={
            "hash": path_insert.excluded.hash,
            "root": path_insert.excluded.root,
            "relative": path_insert.excluded.relative,
        },
    )

    delete_path = delete(path_table).where(path_table.c.path == bindparam("stale_path"))

    with engine.begin() as connection:
        cached_paths = set(connection.scalars(select(path_table.c.path)))

        if path_rows:
            connection.execute(
                path_upsert,
                [{
                    "path": row.path,
                    "hash": row.hash,
                    "root": row.root,
                    "relative": row.relative,
                } for row in path_rows.values()],
            )

        stale_paths = cached_paths - path_rows.keys()

        if stale_paths:
            connection.execute(
                delete_path,
                [{
                    "stale_path": path
                } for path in stale_paths],
            )


def store_missing_file_columns(
    ctx: RunContext,
    engine: Engine,
    file_table: Table,
    file_paths: Sequence[_FilePathRow],
    missing_rows: Sequence[_FilePathRow],
    db: IndexDatabase,
    indexers: Sequence[BaseIndexer],
    columns: Sequence[FileTreeColumnSpec],
) -> None:
    if not missing_rows:
        return

    missing_hashes = list(dict.fromkeys(row.hash for row in missing_rows))
    assets_by_hash = fetch_indexer_assets(ctx, db, missing_hashes, indexers)

    rows: list[dict[str, str]] = []

    with ctx.trace_scope("populate file tree cache", file_count=len(missing_rows)):
        for row_data in missing_rows:
            path = Path(row_data.path)
            file_hash = FileHash(hash=row_data.hash)
            assets = assets_by_hash[row_data.hash]

            row: dict[str, str] = {
                "hash": row_data.hash,
                "root": row_data.root,
                "relative": row_data.relative,
            }

            for column in columns:
                assert path.exists(), str(path)
                data = column.initColumnData(
                    args=FileTreeInitArgs(
                        path=path,
                        hash=file_hash,
                        is_directory=False,
                        root=row_data.root,
                        relative=row_data.relative,
                    ),
                    assets=assets,
                    nested=[],
                )

                if "file_name" == column.column_name:
                    assert str(path).endswith(data.name), f"{path} --> {data.name}"

                row[column.column_name] = json.dumps(
                    None if data is None else model_to_json_data(data),
                    separators=(",", ":"),
                )

            rows.append(row)

    with engine.begin() as connection:
        connection.execute(
            sqlite_insert(file_table).on_conflict_do_nothing(index_elements=[
                file_table.c.hash,
                file_table.c.root,
                file_table.c.relative,
            ],),
            rows,
        )


def populate_cache(
    ctx: RunContext,
    db: IndexDatabase,
    engine: Engine,
    file_table: Table,
    path_table: Table,
    file_paths: Sequence[_FilePathRow],
    indexers: Sequence[BaseIndexer],
    columns: Sequence[FileTreeColumnSpec],
) -> None:
    _store_path_hashes(engine, path_table, file_paths)

    missing_rows = _get_uncached_rows(
        engine,
        file_table,
        file_paths,
    )

    store_missing_file_columns(
        ctx,
        engine,
        file_table,
        file_paths,
        missing_rows,
        db,
        indexers,
        columns,
    )
