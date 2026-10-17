from pathlib import Path

import imagehash
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from loguru import logger
from PIL import Image
from sqlalchemy import MetaData, String, insert, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

IMAGE_HASH_SCHEMA = MetaData()


class IMAGE_HASH_BASE(DeclarativeBase):
    metadata = IMAGE_HASH_SCHEMA


class ImageHash(IMAGE_HASH_BASE):
    __tablename__ = "image_hash"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    average: Mapped[str] = mapped_column(String(16), nullable=False)
    perceptual: Mapped[str] = mapped_column(String(16), nullable=False)
    difference: Mapped[str] = mapped_column(String(16), nullable=False)
    wavelet: Mapped[str] = mapped_column(String(16), nullable=False)


class ImageHashIndexer(BaseIndexer):
    asset_name = "image_hash"
    result_schema = IMAGE_HASH_SCHEMA
    max_parallel = 16

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}

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

        path = ctx.get_path(request.file_ref)
        logger.debug("Image hash for '{}'", path)

        with Image.open(path) as image:
            image.load()
            statement = insert(ImageHash).values(
                hash=file_hash,
                average=str(imagehash.average_hash(image)),
                perceptual=str(imagehash.phash(image)),
                difference=str(imagehash.dhash(image)),
                wavelet=str(imagehash.whash(image)),
            )

        with self.database.transaction() as connection:
            existing = connection.scalar(
                select(ImageHash.hash).where(ImageHash.hash == file_hash)
            )

            if existing is not None:
                return

            connection.execute(statement)
