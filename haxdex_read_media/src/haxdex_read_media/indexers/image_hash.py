from pathlib import Path

import imagehash
from haxdex_core.database import IndexerDatabase
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from loguru import logger
from PIL import Image
from sqlalchemy import Column, MetaData, String, Table, select

_metadata = MetaData()

_image_hash = Table(
    "image_hash",
    _metadata,
    Column("hash", String(64), primary_key=True),
    Column("average", String(16), nullable=False),
    Column("perceptual", String(16), nullable=False),
    Column("difference", String(16), nullable=False),
    Column("wavelet", String(16), nullable=False),
)


class ImageHashIndexer(BaseIndexer):
    asset_name = "image_hash"
    result_schema = _metadata
    max_parallel = 16

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerDatabase],
    ) -> None:
        file_hash = request.get_hash_str()

        if self.database.has_file(file_hash):
            return

        path = ctx.get_path(request.file_ref)
        logger.debug("Image hash for '{}'", path)

        with Image.open(path) as image:
            image.load()
            values = {
                "hash": file_hash,
                "average": str(imagehash.average_hash(image)),
                "perceptual": str(imagehash.phash(image)),
                "difference": str(imagehash.dhash(image)),
                "wavelet": str(imagehash.whash(image)),
            }

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(_image_hash.c.hash).where(_image_hash.c.hash == file_hash)
            ).first()

            if existing is not None:
                return

            connection.execute(_image_hash.insert().values(**values))
