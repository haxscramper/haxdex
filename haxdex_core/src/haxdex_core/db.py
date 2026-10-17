from __future__ import annotations

from pathlib import Path
from typing import Self

from loguru import logger
from sqlalchemy import Column, MetaData, String, Table, and_, select

from haxdex_core.database import IndexerStore
from haxdex_core.hash_cache import HashCache
from haxdex_core.types import FileHash, FileRef, RootRef


def _catalog_schema() -> MetaData:
    metadata = MetaData()

    Table(
        "roots",
        metadata,
        Column("name", String, primary_key=True),
        Column("path", String, nullable=False),
    )

    Table(
        "files",
        metadata,
        Column("hash", String(64), primary_key=True),
    )

    Table(
        "file_paths",
        metadata,
        Column("file_hash", String(64), primary_key=True),
        Column("root_name", String, primary_key=True),
        Column("relative", String, primary_key=True),
        Column("suffix", String, nullable=False),
        Column("name", String, nullable=False),
    )

    return metadata


class IndexDatabase:
    def __init__(self, directory: Path, hash_cache: HashCache) -> None:
        self.directory = directory.expanduser().absolute()
        self.directory.mkdir(parents=True, exist_ok=True)

        self.hash_cache = hash_cache
        self._indexer_databases: dict[str, IndexerStore] = {}

        self._catalog = IndexerStore(
            path=self.directory / "catalog.duckdb",
            asset_name="files",
            result_schema=_catalog_schema(),
            reset_db=False,
        )

        self._roots_table = self._catalog.result_schema.tables["roots"]
        self._files_table = self._catalog.result_schema.tables["files"]
        self._paths_table = self._catalog.result_schema.tables["file_paths"]

        with self._catalog.connection() as connection:
            rows = connection.execute(select(self._roots_table)).mappings().all()

        self.roots: dict[str, Path] = {row["name"]: Path(row["path"]) for row in rows}

    def open_indexer(
        self,
        asset_name: str,
        result_schema: MetaData,
        reset_db: bool,
    ) -> IndexerStore:
        existing = self._indexer_databases.get(asset_name)
        if existing is not None:
            if existing.result_schema is not result_schema:
                raise ValueError(
                    f"Indexer {asset_name!r} is already open with another MetaData"
                )
            return existing

        database = IndexerStore(
            path=self.directory / "indexers" / f"{asset_name}.duckdb",
            asset_name=asset_name,
            result_schema=result_schema,
            reset_db=reset_db,
        )
        logger.info(f"Opening indexer {asset_name} to {database.path}")
        self._indexer_databases[asset_name] = database
        return database

    def get_indexer_database(self, asset_name: str) -> IndexerStore:
        return self._indexer_databases[asset_name]

    def has_indexer_result(
        self,
        ref: FileHash | FileRef,
        indexer: str,
    ) -> bool:
        return self.get_indexer_database(indexer).has_file(ref)

    def add_root(self, name: str, root: Path) -> RootRef:
        root = root.expanduser().absolute()

        if not root.is_dir():
            raise NotADirectoryError(root)

        known = self.roots.get(name)
        if known is not None:
            if known != root:
                raise ValueError(f"Root {name!r} already refers to {known}, not {root}")
            return RootRef(name=name)

        with self._catalog.transaction() as connection:
            connection.execute(
                self._roots_table.insert().values(
                    name=name,
                    path=str(root),
                )
            )

        self.roots[name] = root
        return RootRef(name=name)

    def get_root(self, root: RootRef) -> Path:
        return self.roots[root.name]

    def get_path(self, ref: FileRef) -> Path:
        return self.get_root(ref.root) / ref.relative

    def as_ref(self, root: RootRef, path: Path) -> FileRef:
        root_path = self.get_root(root)
        full_path = path.expanduser().absolute()
        relative = str(full_path.relative_to(root_path))
        file_hash = self.hash_cache.hash(full_path)

        ref = FileRef(
            hash=file_hash,
            relative=relative,
            root=root,
        )

        path_condition = and_(
            self._paths_table.c.file_hash == file_hash.hash,
            self._paths_table.c.root_name == root.name,
            self._paths_table.c.relative == relative,
        )

        with self._catalog.transaction() as connection:
            file_exists = connection.execute(
                select(self._files_table.c.hash).where(
                    self._files_table.c.hash == file_hash.hash
                )
            ).first()

            if file_exists is None:
                connection.execute(
                    self._files_table.insert().values(hash=file_hash.hash)
                )

            path_exists = connection.execute(
                select(self._paths_table.c.file_hash).where(path_condition)
            ).first()

            if path_exists is None:
                connection.execute(
                    self._paths_table.insert().values(
                        file_hash=file_hash.hash,
                        root_name=root.name,
                        relative=relative,
                        suffix=full_path.suffix,
                        name=full_path.name,
                    )
                )

        return ref

    def get_all_refs(self, file_hash: FileHash) -> list[FileRef]:
        with self._catalog.connection() as connection:
            rows = (
                connection.execute(
                    select(
                        self._paths_table.c.root_name,
                        self._paths_table.c.relative,
                    ).where(self._paths_table.c.file_hash == file_hash.hash)
                )
                .mappings()
                .all()
            )

        return [
            FileRef(
                hash=file_hash,
                relative=row["relative"],
                root=RootRef(name=row["root_name"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        for database in self._indexer_databases.values():
            database.close()

        self._catalog.close()
        self.hash_cache.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @staticmethod
    def reset_database(directory: Path) -> None:
        """Remove managed database files. All connections must be closed."""
        directory = directory.expanduser().absolute()
        paths = [
            directory / "catalog.duckdb",
            *(directory / "indexers").glob("*.duckdb"),
        ]

        for path in paths:
            Path(str(path) + ".wal").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
