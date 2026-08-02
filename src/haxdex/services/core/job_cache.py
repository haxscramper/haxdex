import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from haxdex.services.core.job_types import BaseIndexer, RunContext
from haxdex.services.core.types import IndexerOutput
from haxdex.services.pydantic_utils import model_to_json_data, model_from_json_data, format_json_with_fjson
from haxdex.services.utils import ExceptionContextNote

log = logging.getLogger(__name__)


def get_schema_hash(indexer: BaseIndexer) -> str:
    return hashlib.sha256(
        json.dumps(
            indexer.result_model.model_json_schema(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),).hexdigest()


def parse_indexer_output(indexer: BaseIndexer, data: dict) -> IndexerOutput:
    """Parse a serialized IndexerOutput dump using the indexer's own result
    model for the 'processed' payload."""
    assert data["indexer_id"] == indexer.asset_name, (
        f"cached indexer id '{data['indexer_id']}' does not match "
        f"'{indexer.asset_name}'")
    with ExceptionContextNote(lambda: format_json_with_fjson(data, max_width=200)):
        return IndexerOutput(
            indexer_id=indexer.asset_name,
            result=model_from_json_data(data["result"], indexer.result_model),
        )


def has_cached_result(indexer: BaseIndexer, file_hash: str) -> bool:
    """Planning-stage check: does the SQLite cache hold a schema-compatible
    result for this (indexer, file)?"""
    if not indexer.should_load_cache:
        return False

    with indexer.database.connect() as database:
        row = database.execute(
            select(indexer.cache_table.c.schema_hash).where(
                indexer.cache_table.c.file_hash == file_hash,),).one_or_none()

    return row is not None and row[0] == get_schema_hash(indexer)


def load_cached_output(
    ctx: RunContext,
    indexer: BaseIndexer,
    file_hash: str,
) -> IndexerOutput | None:
    """Execution-stage load of a full cached output. Returns None when the
    entry is missing, schema-stale or unparsable."""
    schema_hash = get_schema_hash(indexer)

    with (
            ctx.trace_scope(
                "load cache database record",
                indexer=indexer.asset_name,
                file_hash=file_hash,
            ),
            indexer.database.connect() as database,
    ):
        cache_row = database.execute(
            select(
                indexer.cache_table.c.schema_hash,
                indexer.cache_table.c.result,
            ).where(
                indexer.cache_table.c.file_hash == file_hash,),).mappings().one_or_none()

    if cache_row is None:
        return None

    if cache_row["schema_hash"] != schema_hash:
        log.info(
            "Cache schema mismatch for indexer {} "
            "(cached={}, current={}), recomputing.".format(
                indexer.asset_name,
                cache_row["schema_hash"],
                schema_hash,
            ),)
        return None

    try:
        return parse_indexer_output(indexer, cache_row["result"])

    except json.JSONDecodeError as err:
        log.error(
            f"Could not parse cached database value for "
            f"{indexer.asset_name}: {err}",)
        return None


def store_cached_output(
    ctx: RunContext,
    indexer: BaseIndexer,
    file_hash: str,
    result: IndexerOutput,
    *,
    function_started_at: datetime,
    function_duration_seconds: float,
) -> None:
    result_json = model_to_json_data(result)
    assert isinstance(result_json, dict)

    upsert = sqlite_insert(indexer.cache_table).values(
        file_hash=file_hash,
        schema_hash=get_schema_hash(indexer),
        result=result_json,
        function_started_at=function_started_at,
        function_duration_seconds=function_duration_seconds,
    )

    upsert = upsert.on_conflict_do_update(
        index_elements=[indexer.cache_table.c.file_hash],
        set_={
            "schema_hash": upsert.excluded.schema_hash,
            "result": upsert.excluded.result,
            "function_started_at": upsert.excluded.function_started_at,
            "function_duration_seconds": upsert.excluded.function_duration_seconds,
        },
    )

    with (
            ctx.trace_scope(
                "store cache database record",
                indexer=indexer.asset_name,
                file_hash=file_hash,
            ),
            indexer.database.begin() as database,
    ):
        database.execute(upsert)
