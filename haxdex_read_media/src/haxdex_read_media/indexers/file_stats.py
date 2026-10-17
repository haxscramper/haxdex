from datetime import datetime

from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    MetaData,
    String,
    insert,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

FILE_STATS_SCHEMA = MetaData()


class FILE_STATS_BASE(DeclarativeBase):
    metadata = FILE_STATS_SCHEMA


class FileStats(FILE_STATS_BASE):
    __tablename__ = "file_stats"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime: Mapped[float] = mapped_column(Float, nullable=False)
    ctime: Mapped[float] = mapped_column(Float, nullable=False)
    modification_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )


class FileStatsIndexer(BaseIndexer):
    asset_name = "file_stats"
    result_schema = FILE_STATS_SCHEMA

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
            condition = FileStats.hash == file_hash
            existing = connection.execute(
                select(FileStats.hash).where(condition)
            ).scalar_one_or_none()

            if existing is None:
                connection.execute(
                    insert(FileStats).values(
                        hash=file_hash,
                        **values,
                    )
                )
            else:
                connection.execute(update(FileStats).where(condition).values(**values))
