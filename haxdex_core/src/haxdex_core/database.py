from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    URL,
    Connection,
    MetaData,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.pool import StaticPool

from haxdex_core.types import FileHash, FileRef


def get_file_hash(ref: FileHash | FileRef | str) -> str:
    if isinstance(ref, str):
        return ref

    if isinstance(ref, FileHash):
        return ref.hash

    return ref.hash.hash


class IndexerStore:
    def __init__(
        self,
        path: Path,
        asset_name: str,
        result_schema: MetaData,
    ) -> None:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", asset_name) is None:
            raise ValueError(f"Invalid asset name: {asset_name!r}")

        table = result_schema.tables.get(asset_name)
        if table is None:
            raise ValueError(
                f"Schema must contain an unqualified table named {asset_name!r}"
            )

        columns = list(table.columns)
        if not columns:
            raise ValueError(f"Table {asset_name!r} must contain a hash column")

        hash_column = columns[0]
        if not isinstance(hash_column.type, String):
            raise TypeError(
                f"The first column of {asset_name!r} must be a SQL string column"
            )

        if hash_column.nullable:
            raise ValueError(f"The first column of {asset_name!r} must be non-nullable")

        self.path = path.expanduser().absolute()
        self.asset_name = asset_name
        self.result_schema = result_schema
        self.main_table: Table = table
        self.hash_column = hash_column

        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._engine = create_engine(
            URL.create("duckdb", database=str(self.path)),
            poolclass=StaticPool,
        )

        try:
            with self.transaction() as connection:
                self.result_schema.create_all(connection)
        except BaseException:
            self._engine.dispose()
            raise

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self._lock:
            with self._engine.connect() as connection:
                yield connection

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._lock:
            with self._engine.begin() as connection:
                yield connection

    def has_file(self, ref: FileHash | FileRef | str) -> bool:
        statement = (
            select(self.hash_column)
            .where(self.hash_column == get_file_hash(ref))
            .limit(1)
        )

        with self.connection() as connection:
            return connection.execute(statement).first() is not None

    def close(self) -> None:
        with self._lock:
            self._engine.dispose()
