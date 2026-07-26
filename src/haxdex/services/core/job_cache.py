from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from beartype import beartype
from beartype.typing import Any, Optional
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from haxdex.services.core.job_types import BaseIndexer
from haxdex.services.core.types import FileHash, FileRef, IndexerOutput
from haxdex.services.pydantic_utils import model_from_json_data, model_to_json_data
from haxdex.services.utils import get_xdg_cache_dir, ExceptionContextNote

log = logging.getLogger(__name__)


@beartype
class JobCache:

    def __init__(self, cache_file: Optional[Path] = None) -> None:
        default_path = get_xdg_cache_dir(["haxdex"]) / "indexer_cache.sqlite"
        self.cache_file = cache_file or default_path
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._meta_results: dict[tuple[str, str], IndexerOutput] = {}
        self._engine: Optional[Engine] = None
        self._available = False

        metadata = MetaData()
        self._table = Table(
            "indexer_result_cache",
            metadata,
            Column("file_hash", String, primary_key=True),
            Column("indexer_id", String, primary_key=True),
            Column("result", JSON, nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )

        try:
            self._engine = create_engine(f"sqlite:///{self.cache_file}")
            metadata.create_all(self._engine, checkfirst=True)
            self._available = True
        except Exception as err:
            self._available = False
            self._engine = None
            log.warning(f"sqlite cache unavailable at {self.cache_file}: {err}")

    def is_available(self) -> bool:
        return self._available

    def _normalize_hash(self, ref: FileHash | FileRef) -> str:
        match ref:
            case FileHash():
                return ref.hash
            case FileRef():
                return ref.hash.hash
            case _:
                raise TypeError(f"unsupported ref type {type(ref)} for cache access")

    def _normalize_indexer(self, indexer: BaseIndexer | str) -> str:
        match indexer:
            case str():
                return indexer
            case BaseIndexer():
                return indexer.asset_name
            case _:
                raise TypeError(
                    f"unsupported indexer type {type(indexer)} for cache access")

    def has_output(self, ref: FileHash | FileRef, indexer: BaseIndexer | str) -> bool:
        file_hash = self._normalize_hash(ref)
        indexer_id = self._normalize_indexer(indexer)
        key = (file_hash, indexer_id)

        if key in self._meta_results:
            return True

        if not self._available or self._engine is None:
            return False

        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._table.c.file_hash).where(
                    self._table.c.file_hash == file_hash,
                    self._table.c.indexer_id == indexer_id,
                )).first()
        return row is not None

    def get_output(
        self,
        ref: FileHash | FileRef,
        indexer: BaseIndexer | str,
    ) -> Optional[IndexerOutput]:
        file_hash = self._normalize_hash(ref)
        indexer_id = self._normalize_indexer(indexer)
        key = (file_hash, indexer_id)

        if key in self._meta_results:
            return self._meta_results[key]

        if not self._available or self._engine is None:
            return None

        with self._engine.connect() as connection:
            row = connection.execute(
                select(self._table.c.result).where(
                    self._table.c.file_hash == file_hash,
                    self._table.c.indexer_id == indexer_id,
                )).mappings().one_or_none()

        if row is None:
            return None

        parsed = model_from_json_data(row["result"], IndexerOutput)
        return parsed

    def store_output(self, ref: FileHash | FileRef, output: IndexerOutput) -> None:
        file_hash = self._normalize_hash(ref)
        payload = model_to_json_data(output)
        assert isinstance(payload, dict), (
            f"cache payload must be a dict, got {type(payload)} for {output.indexer_id}")

        self._meta_results[(file_hash, output.indexer_id)] = output

        if not self._available or self._engine is None:
            return

        upsert = sqlite_insert(self._table).values(
            file_hash=file_hash,
            indexer_id=output.indexer_id,
            result=payload,
            updated_at=datetime.now(timezone.utc),
        )
        upsert = upsert.on_conflict_do_update(
            index_elements=[self._table.c.file_hash, self._table.c.indexer_id],
            set_={
                "result": upsert.excluded.result,
                "updated_at": upsert.excluded.updated_at,
            },
        )

        with self._engine.begin() as connection:
            connection.execute(upsert)

    def register_meta_results(self, ref: FileRef, results: dict[str, dict[str,
                                                                          Any]]) -> None:
        for indexer_id, result_payload in results.items():
            parsed = model_from_json_data(result_payload, IndexerOutput)
            if parsed.indexer_id != indexer_id:
                raise ValueError(
                    f"meta cache result indexer mismatch for {ref.hash.hash}: "
                    f"dict key is '{indexer_id}', payload indexer_id is '{parsed.indexer_id}'"
                )
            self._meta_results[(ref.hash.hash, indexer_id)] = parsed

    def register_meta_file(self, ref: FileRef, meta_file: Path) -> None:
        payload = json.loads(meta_file.read_text())
        if "results" not in payload:
            raise ValueError(f"meta file '{meta_file}' does not contain 'results' key")
        results = payload["results"]
        if not isinstance(results, dict):
            raise TypeError(
                f"meta file '{meta_file}' has non-dict results value: {type(results)}")

        with ExceptionContextNote(f"loading {meta_file}"):
            self.register_meta_results(ref, results)
