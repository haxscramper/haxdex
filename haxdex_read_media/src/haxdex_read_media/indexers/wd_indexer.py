from pathlib import Path
from typing import cast

from haxdex_core.database import IndexerDatabase
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from haxdex_read_media.resources.wd_tagger import WdTagger
from loguru import logger
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    select,
)

wd_tags_schema = MetaData()

wd_tags = Table(
    "wd_tags",
    wd_tags_schema,
    Column("file_hash", String(64), primary_key=True),
)

wd_tag_entries = Table(
    "wd_tag_entries",
    wd_tags_schema,
    Column(
        "file_hash",
        String(64),
        ForeignKey("wd_tags.file_hash"),
        primary_key=True,
    ),
    Column(
        "position",
        Integer,
        primary_key=True,
        autoincrement=False,
    ),
    Column("category", String, nullable=False),
    Column("name", String, nullable=False),
    Column("probability", Float, nullable=False),
)


class WdTagIndexer(BaseIndexer):
    asset_name = "wd_tags"
    result_schema = wd_tags_schema
    required_resources = ("wd_tagger",)

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
        logger.debug("Tagging {}", path)

        tagger = cast(WdTagger, resources["wd_tagger"])
        tags = tagger.tag_image(ctx, path)

        rows = [
            {
                "file_hash": file_hash,
                "position": position,
                "category": tag.category,
                "name": tag.name,
                "probability": tag.probability,
            }
            for position, tag in enumerate(tags)
        ]

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(wd_tags.c.file_hash).where(wd_tags.c.file_hash == file_hash)
            ).first()

            if existing is not None:
                return

            connection.execute(wd_tags.insert().values(file_hash=file_hash))

            if rows:
                connection.execute(wd_tag_entries.insert(), rows)
