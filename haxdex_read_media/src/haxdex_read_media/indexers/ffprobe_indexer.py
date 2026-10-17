import json
from pathlib import Path
from typing import Any, ClassVar

import magic
from beartype import beartype
from haxdex_core.database import IndexerDatabase
from haxdex_core.job_types import (
    BaseIndexer,
    BaseIndexerConfig,
    BaseResource,
    RunContext,
)
from haxdex_core.types import IndexerRequest
from loguru import logger
from plumbum import local
from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    select,
)

ffprobe_schema = MetaData()

ffprobe_table = Table(
    "ffprobe",
    ffprobe_schema,
    Column("hash", String(64), primary_key=True),
    Column("duration_seconds", Float),
    Column("bitrate_bps", BigInteger),
    Column("width", Integer),
    Column("height", Integer),
    Column("resolution", String),
    Column("codec_name", String),
    Column("format_name", String),
    Column("fps", Float),
)


@beartype
class FFProbeInfoModel(BaseModel, extra="forbid"):
    duration_seconds: float | None = None
    bitrate_bps: int | None = None
    width: int | None = None
    height: int | None = None
    resolution: str | None = None
    codec_name: str | None = None
    format_name: str | None = None
    fps: float | None = None


@beartype
def run_ffprobe(path: Path) -> FFProbeInfoModel:
    logger.debug("running probe on {}", path)

    result = local["ffprobe"].run(
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )

    payload = json.loads(result[1])
    streams_raw = payload.get("streams", [])
    format_raw = payload.get("format", {})

    video_stream = next(
        (stream for stream in streams_raw if stream.get("codec_type") == "video"),
        None,
    )

    def parse_int(value: Any) -> int | None:
        if value is None or value == "N/A":
            return None
        return int(value)

    def parse_float(value: Any) -> float | None:
        if value is None or value == "N/A":
            return None
        return float(value)

    def parse_fps(value: Any) -> float | None:
        if value is None or value == "N/A":
            return None

        text = str(value)
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            divisor = float(denominator)
            if divisor == 0:
                return None
            return float(numerator) / divisor

        return float(text)

    width = parse_int(video_stream.get("width")) if video_stream is not None else None
    height = parse_int(video_stream.get("height")) if video_stream is not None else None
    resolution = (
        f"{width}x{height}" if width is not None and height is not None else None
    )
    fps = (
        parse_fps(video_stream.get("avg_frame_rate"))
        if video_stream is not None
        else None
    )

    return FFProbeInfoModel(
        duration_seconds=parse_float(format_raw.get("duration")),
        bitrate_bps=parse_int(format_raw.get("bit_rate")),
        width=width,
        height=height,
        resolution=resolution,
        codec_name=(
            video_stream.get("codec_name") if video_stream is not None else None
        ),
        format_name=format_raw.get("format_name"),
        fps=fps,
    )


class FFProbeIndexer(BaseIndexer):
    asset_name = "ffprobe"
    result_schema: ClassVar[MetaData] = ffprobe_schema

    def __init__(
        self,
        config: BaseIndexerConfig,
        database: IndexerDatabase,
    ) -> None:
        super().__init__(config=config, database=database)
        self._magic = magic.Magic(mime=True)

    def can_run(self, path: Path) -> bool:
        mime = self._magic.from_file(str(path.absolute()))
        return mime.startswith("video/")

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

        probe = run_ffprobe(ctx.get_path(request.file_ref))

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(ffprobe_table.c.hash).where(ffprobe_table.c.hash == file_hash)
            ).first()

            if existing is not None:
                return

            connection.execute(
                ffprobe_table.insert().values(
                    hash=file_hash,
                    **probe.model_dump(),
                )
            )
