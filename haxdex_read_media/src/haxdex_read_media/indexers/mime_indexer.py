import ctypes

import magic
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import (
    BaseIndexer,
    BaseIndexerConfig,
    BaseResource,
    RunContext,
)
from haxdex_core.types import IndexerRequest
from magic import Magic, libmagic
from sqlalchemy import ARRAY, MetaData, String, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    Session,
    mapped_column,
)

# From file/magic.h.
MAGIC_PARAM_NAME_MAX = 5

FILE_MIME_SCHEMA = MetaData()


class FILE_MIME_BASE(MappedAsDataclass, DeclarativeBase):
    metadata = FILE_MIME_SCHEMA


class FileMime(FILE_MIME_BASE):
    __tablename__ = "file_mime"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    mime_encoding: Mapped[str] = mapped_column(String, nullable=False)
    magic_description: Mapped[str] = mapped_column(String, nullable=False)
    magic_all_matches: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
    )
    magic_extensions: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
    )


def _set_name_max(magic_instance: Magic, value: int) -> None:
    val = ctypes.c_size_t(value)
    libmagic.magic_setparam(
        magic_instance.cookie,
        MAGIC_PARAM_NAME_MAX,
        ctypes.byref(val),
    )


class FileMimeIndexer(BaseIndexer):
    asset_name = FileMime.__tablename__
    result_schema = FILE_MIME_SCHEMA
    table = FileMime.__table__

    def __init__(
        self,
        config: BaseIndexerConfig,
        database: IndexerStore,
    ) -> None:
        super().__init__(config=config, database=database)

        self._magic_mime = Magic(mime=True)
        self._magic_encoding = Magic(mime_encoding=True)
        self._magic_description = Magic()
        self._magic_all = Magic(keep_going=True)
        self._magic_extensions = Magic(extension=True)

        for instance in (
            self._magic_mime,
            self._magic_encoding,
            self._magic_description,
            self._magic_all,
            self._magic_extensions,
        ):
            _set_name_max(instance, 256)

    @staticmethod
    def _from_file(
        magic_instance: Magic,
        path_str: str,
        default: str = "",
    ) -> str:
        try:
            return magic_instance.from_file(path_str)
        except magic.MagicException as error:
            raw = error.args[0] if error.args else b""
            message = (
                raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            )
            # libmagic can return a valid, fully-formed description and still
            # signal an error (e.g. "name use count (64) exceeded"). Salvage
            # the usable prefix instead of failing the whole indexer.
            cleaned = message.split(" name use count ")[0].strip()
            return cleaned or default

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

        path_str = str(ctx.get_path(request.file_ref))

        mime_type = self._from_file(self._magic_mime, path_str)
        mime_encoding = self._from_file(self._magic_encoding, path_str)
        magic_description = self._from_file(
            self._magic_description,
            path_str,
        )

        all_matches_raw = self._from_file(self._magic_all, path_str)
        magic_all_matches = [
            line.strip() for line in all_matches_raw.splitlines() if line.strip()
        ]

        extensions_raw = self._from_file(
            self._magic_extensions,
            path_str,
        )
        magic_extensions = [
            extension.strip()
            for extension in extensions_raw.split("/")
            if extension.strip()
        ]

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            # Another reference with identical content may already have run.
            existing = session.scalar(
                select(FileMime.hash).where(FileMime.hash == file_hash)
            )

            if existing is not None:
                return

            session.add(
                FileMime(
                    hash=file_hash,
                    mime_type=mime_type,
                    mime_encoding=mime_encoding,
                    magic_description=magic_description,
                    magic_all_matches=magic_all_matches,
                    magic_extensions=magic_extensions,
                )
            )
            session.flush()
