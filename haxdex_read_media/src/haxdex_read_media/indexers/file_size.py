from haxdex_core.database import IndexerDatabase
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from sqlalchemy import BigInteger, Column, MetaData, String, Table, select


class FileSizeIndexer(BaseIndexer):
    asset_name = "file_size"
    result_schema = MetaData()

    table = Table(
        asset_name,
        result_schema,
        Column("hash", String(64), primary_key=True),
        Column("size_bytes", BigInteger, nullable=False),
    )

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerDatabase],
    ) -> None:
        file_hash = request.get_hash_str()
        size = ctx.get_path(request.file_ref).stat().st_size

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(self.table.c.hash).where(self.table.c.hash == file_hash)
            ).first()

            if existing is not None:
                return

            connection.execute(
                self.table.insert().values(
                    hash=file_hash,
                    size_bytes=size,
                )
            )
