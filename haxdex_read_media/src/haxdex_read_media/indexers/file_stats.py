from datetime import datetime

from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    MetaData,
    String,
    Table,
    select,
)

result_schema = MetaData()

file_stats = Table(
    "file_stats",
    result_schema,
    Column("hash", String(64), primary_key=True),
    Column("size_bytes", BigInteger, nullable=False),
    Column("mode", BigInteger, nullable=False),
    Column("mtime", Float, nullable=False),
    Column("ctime", Float, nullable=False),
    Column("modification_time", DateTime, nullable=False),
)


class FileStatsIndexer(BaseIndexer):
    asset_name = "file_stats"
    result_schema = result_schema

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        stat = ctx.get_path(request.file_ref).stat()
        file_hash = request.get_hash_str()
        values = {
            "size_bytes": stat.st_size,
            "mode": stat.st_mode,
            "mtime": stat.st_mtime,
            "ctime": stat.st_ctime,
            "modification_time": datetime.fromtimestamp(stat.st_mtime),
        }

        with self.database.transaction() as connection:
            condition = file_stats.c.hash == file_hash
            existing = connection.execute(
                select(file_stats.c.hash).where(condition)
            ).first()

            if existing is None:
                connection.execute(
                    file_stats.insert().values(
                        hash=file_hash,
                        **values,
                    )
                )
            else:
                connection.execute(
                    file_stats.update().where(condition).values(**values)
                )
