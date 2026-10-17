import subprocess
import tempfile
from pathlib import Path
from typing import cast

import magic
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import (
    BaseIndexer,
    BaseIndexerConfig,
    BaseResource,
    RunContext,
)
from haxdex_core.types import IndexerRequest
from sqlalchemy import ForeignKey, MetaData, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

MEDIA_TRANSCRIPTION_SCHEMA = MetaData()


class MEDIA_TRANSCRIPTION_BASE(DeclarativeBase):
    metadata = MEDIA_TRANSCRIPTION_SCHEMA


class MediaTranscription(MEDIA_TRANSCRIPTION_BASE):
    __tablename__ = "media_transcription"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    mime: Mapped[str]
    ok: Mapped[bool]
    model: Mapped[str]
    text: Mapped[str]
    language: Mapped[str | None]
    duration_sec: Mapped[float | None]
    vtt: Mapped[str]
    error: Mapped[str | None]


class MediaTranscriptionSegment(MEDIA_TRANSCRIPTION_BASE):
    __tablename__ = "media_transcription_segments"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("media_transcription.hash"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=False,
    )
    index: Mapped[int]
    start_sec: Mapped[float]
    end_sec: Mapped[float]
    text: Mapped[str]


class MediaTranscriptionIndexer(BaseIndexer):
    asset_name = "media_transcription"
    result_schema = MEDIA_TRANSCRIPTION_SCHEMA
    required_resources = ("whisper_transcribe",)

    def __init__(
        self,
        config: BaseIndexerConfig,
        database: IndexerStore,
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
        assets: dict[str, IndexerStore],
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

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            existing = session.scalar(
                select(MediaTranscription.hash).where(
                    MediaTranscription.hash == file_hash
                )
            )

            if existing is not None:
                return

            session.add(
                MediaTranscription(
                    hash=file_hash,
                    mime=mime,
                    ok=result.ok,
                    model=result.model,
                    text=result.text,
                    language=result.language,
                    duration_sec=result.duration_sec,
                    vtt=result.vtt,
                    error=result.error,
                )
            )
            session.flush()

            session.add_all(
                [
                    MediaTranscriptionSegment(
                        file_hash=file_hash,
                        position=position,
                        index=segment.index,
                        start_sec=segment.start_sec,
                        end_sec=segment.end_sec,
                        text=segment.text,
                    )
                    for position, segment in enumerate(result.segments)
                ]
            )
            session.flush()
