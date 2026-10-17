from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from sqlalchemy import BigInteger, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

FILE_SIZE_SCHEMA = MetaData()


class FILE_SIZE_BASE(DeclarativeBase):
    metadata = FILE_SIZE_SCHEMA


class FileSize(FILE_SIZE_BASE):
    __tablename__ = "file_size"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class FileSizeIndexer(BaseIndexer):
    asset_name = "file_size"
    result_schema = FILE_SIZE_SCHEMA
    table = FileSize.__table__

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()
        size = ctx.get_path(request.file_ref).stat().st_size

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            existing = session.get(FileSize, file_hash)

            if existing is not None:
                return

            session.add(
                FileSize(
                    hash=file_hash,
                    size_bytes=size,
                )
            )
            session.flush()
