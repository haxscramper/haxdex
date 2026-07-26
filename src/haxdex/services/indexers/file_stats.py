from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine

from haxdex.services.core.job_types import BaseIndexer, BaseIndexerConfig, RunContext
from haxdex.services.core.types import IndexDocument, IndexerOutput, IndexerRequest
from pydantic import BaseModel, Field


class FileStatsIndexerResult(IndexDocument, extra="forbid"):
    size_bytes: int = Field(ge=0)
    mode: int = Field(ge=0)
    mtime: float = Field(ge=0)
    ctime: float = Field(ge=0)
    modification_time: str = Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?$")


class FileStatsIndexer(BaseIndexer):
    asset_name = "file_stats"
    result_model = FileStatsIndexerResult

    def __init__(self, config: BaseIndexerConfig, database: Engine) -> None:
        super().__init__(config=config, database=database)

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, object],
        assets: dict[str, object],
    ) -> IndexerOutput:
        st = ctx.get_path(request.file_ref).stat()
        return IndexerOutput(
            indexer_id=self.asset_name,
            result=FileStatsIndexerResult(
                hash=request.get_hash_str(),
                size_bytes=st.st_size,
                mode=st.st_mode,
                mtime=st.st_mtime,
                ctime=st.st_ctime,
                modification_time=datetime.fromtimestamp(st.st_mtime).isoformat(),
            ),
        )
