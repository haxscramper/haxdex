import subprocess
import tempfile
from pathlib import Path
from typing import cast

import magic
from haxdex_core.database import IndexerDatabase
from haxdex_core.job_types import (
    BaseIndexer,
    BaseIndexerConfig,
    BaseResource,
    RunContext,
)
from haxdex_core.types import IndexerRequest
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    select,
)

_metadata = MetaData()

_media_transcription = Table(
    "media_transcription",
    _metadata,
    Column("hash", String(64), primary_key=True),
    Column("mime", String, nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("model", String, nullable=False),
    Column("text", String, nullable=False),
    Column("language", String, nullable=True),
    Column("duration_sec", Float, nullable=True),
    Column("vtt", String, nullable=False),
    Column("error", String, nullable=True),
)

_media_transcription_segments = Table(
    "media_transcription_segments",
    _metadata,
    Column(
        "file_hash",
        String(64),
        ForeignKey("media_transcription.hash"),
        primary_key=True,
    ),
    Column(
        "position",
        Integer,
        primary_key=True,
        autoincrement=False,
    ),
    Column("index", Integer, nullable=False),
    Column("start_sec", Float, nullable=False),
    Column("end_sec", Float, nullable=False),
    Column("text", String, nullable=False),
)


class MediaTranscriptionIndexer(BaseIndexer):
    asset_name = "media_transcription"
    result_schema = _metadata
    required_resources = ("whisper_transcribe",)

    def __init__(
        self,
        config: BaseIndexerConfig,
        database: IndexerDatabase,
    ) -> None:
        super().__init__(config=config, database=database)
        self._magic = magic.Magic(mime=True)

    def can_run(self, path: Path) -> bool:
        if not path.is_file():
            return False

        mime = self._magic.from_file(str(path.resolve()))
        return mime.startswith(("audio/", "video/"))

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

        source_path = ctx.get_path(request.file_ref)
        mime = self._magic.from_file(str(source_path.resolve()))
        whisper = cast(
            WhisperTranscribeResource,
            resources["whisper_transcribe"],
        )

        with tempfile.TemporaryDirectory(prefix="index-whisper-") as directory:
            wav_path = Path(directory) / "audio.wav"

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-err_detect",
                    "ignore_err",
                    "-fflags",
                    "+discardcorrupt",
                    "-i",
                    str(source_path),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            result = whisper.handle(
                ctx,
                WhisperTranscribeRequest(wav_path=str(wav_path)),
                resources=cast(dict[str, object], resources),
            )

        values = {
            "hash": file_hash,
            "mime": mime,
            **result.model_dump(exclude={"segments"}),
        }
        segments = [
            {
                "file_hash": file_hash,
                "position": position,
                **segment.model_dump(),
            }
            for position, segment in enumerate(result.segments)
        ]

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(_media_transcription.c.hash).where(
                    _media_transcription.c.hash == file_hash
                )
            ).first()

            if existing is not None:
                return

            connection.execute(_media_transcription.insert().values(**values))

            if segments:
                connection.execute(
                    _media_transcription_segments.insert(),
                    segments,
                )
