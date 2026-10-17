from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Iterator

from pydantic import BaseModel
from sqlalchemy import MetaData

from haxdex_core.database import IndexerDatabase
from haxdex_core.db import IndexDatabase
from haxdex_core.trace import TraceWriter
from haxdex_core.types import FileRef, IndexerRequest


class RunContext:
    def __init__(self, db: IndexDatabase) -> None:
        self._db = db
        self.writer: TraceWriter | None = None

    def get_path(self, ref: FileRef) -> Path:
        return self._db.get_path(ref)

    def start_trace(self) -> None:
        self.writer = TraceWriter()

    @contextmanager
    def trace_scope(self, message: str, **args: Any) -> Iterator[None]:
        writer = self.writer
        if writer is None:
            yield
            return

        writer.begin(message, **args)
        try:
            yield
        finally:
            writer.end(message)


class BaseResourceConfig(BaseModel, extra="forbid"):
    pass


class BaseResource(ABC):
    resource_key: str
    required_resources: tuple[str, ...] = ()
    config_model: ClassVar[type[BaseResourceConfig]] = BaseResourceConfig

    def __init__(self, config: BaseResourceConfig) -> None:
        self.config = config

    @abstractmethod
    def handle(
        self,
        ctx: RunContext,
        request: BaseModel,
        resources: dict[str, BaseResource],
    ) -> BaseModel:
        raise NotImplementedError


class BaseIndexerConfig(BaseModel, extra="forbid"):
    pass


class BaseIndexer(ABC):
    asset_name: str
    result_schema: ClassVar[MetaData]
    required_assets: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    max_parallel: int = 1
    config_model: ClassVar[type[BaseIndexerConfig]] = BaseIndexerConfig

    def __init__(
        self,
        database: IndexerDatabase,
        config: BaseIndexerConfig,
    ) -> None:
        if database.asset_name != self.asset_name:
            raise ValueError(
                f"Database for {database.asset_name!r} cannot be used by "
                f"indexer {self.asset_name!r}"
            )

        if database.result_schema is not self.result_schema:
            raise ValueError(
                f"Indexer {self.asset_name!r} and its database must share MetaData"
            )

        if self.max_parallel < 1:
            raise ValueError("max_parallel must be greater than zero")

        self.database = database
        self.config = config

    def can_run(self, path: Path) -> bool:
        return True

    @abstractmethod
    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerDatabase],
    ) -> None:
        raise NotImplementedError
