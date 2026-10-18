from datasketch import MinHash
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import (
    BaseIndexer,
    BaseIndexerConfig,
    BaseResource,
    RunContext,
)
from haxdex_core.types import IndexerRequest
from pydantic import Field
from sqlalchemy import Integer, LargeBinary, MetaData, String, insert, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

FILE_LSH_SCHEMA = MetaData()


class FILE_LSH_BASE(DeclarativeBase):
    metadata = FILE_LSH_SCHEMA


class FileLshSignature(FILE_LSH_BASE):
    __tablename__ = "file_lsh"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    num_perm: Mapped[int] = mapped_column(Integer, nullable=False)
    shingle_size: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class FileLshIndexerConfig(BaseIndexerConfig, extra="forbid"):
    num_perm: int = Field(
        default=128,
        ge=1,
        description="Number of MinHash permutations; each occupies eight bytes.",
    )
    shingle_size: int = Field(
        default=5,
        ge=1,
        description="Length in bytes of each overlapping document shingle.",
    )


class FileLshIndexer(BaseIndexer):
    asset_name = "file_lsh"
    result_schema = FILE_LSH_SCHEMA
    config_model = FileLshIndexerConfig

    config: FileLshIndexerConfig

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()

        if self.database.has_file(file_hash):
            return

        data = ctx.get_path(request.file_ref).read_bytes()
        shingle_size = self.config.shingle_size
        minhash = MinHash(num_perm=self.config.num_perm, seed=1)

        if data:
            for offset in range(max(1, len(data) - shingle_size + 1)):
                minhash.update(data[offset : offset + shingle_size])

        statement = insert(FileLshSignature).values(
            hash=file_hash,
            num_perm=self.config.num_perm,
            shingle_size=shingle_size,
            signature=minhash.hashvalues.astype("<u8").tobytes(),
        )

        with self.database.transaction() as connection:
            existing = connection.scalar(
                select(FileLshSignature.hash).where(FileLshSignature.hash == file_hash)
            )

            if existing is not None:
                return

            connection.execute(statement)
