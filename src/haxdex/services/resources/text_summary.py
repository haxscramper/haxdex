# haxdex/services/resources/text_summary.py
from loguru import logger
from typing import Any, ClassVar, Literal, Union

from pydantic import BaseModel, Field
from beartype.typing import Annotated

from haxdex.services.core.job_types import (
    BaseResource,
    BaseResourceConfig,
    RunContext,
)
from haxdex.services.core.types import MultiDocumentModel
from haxdex.services.indexers.chunk_indexing.chunking import (
    ChunkConfig,
    ChunkUnit,
    Chunker,
    ChunkDocument,
    ChunkFile,
    ChunkLink,
    chunks_to_multidoc,
)
from haxdex.services.resources.flm_server import (
    FlmMessage,
    FlmRequest,
    FlmServerResource,
)

_SUMMARIZE_SYSTEM_PROMPT = "You summarize text files in 5-10 concise sentences."
_COMBINE_SYSTEM_PROMPT = (
    "You are given summaries of consecutive sections of a single text file. "
    "Produce one coherent summary of the whole file in 5-10 concise sentences.")


class SummarizeRequest(BaseModel, extra="forbid"):
    text: str
    file_hash: str


class SummaryChunk(ChunkDocument, extra="forbid"):
    summary: str


class SummaryFile(ChunkFile, extra="forbid"):
    summary: str = ""


class TextSummaryResult(MultiDocumentModel, extra="forbid"):
    document_type: ClassVar[Any] = Annotated[
        Union[SummaryFile, SummaryChunk],
        Field(discriminator="type"),
    ]
    edge_type: ClassVar[Any] = ChunkLink


class TextSummaryResourceConfig(BaseResourceConfig, extra="forbid"):
    model: str = "gemma4-it:e4b"
    chunk: ChunkConfig = Field(default_factory=lambda: ChunkConfig(
        unit=ChunkUnit.TOKENS,
        max_size=12_000,
        start_overlap_max=200,
        encoding_name="o200k_base",
    ))


class TextSummaryResource(BaseResource):
    resource_key = "text_summary"
    required_resources = ("flm_server",)
    config_model = TextSummaryResourceConfig

    config: TextSummaryResourceConfig

    def __init__(self, config: TextSummaryResourceConfig) -> None:
        super().__init__(config=config)

    def _resolve_flm(self, resources: dict[str, BaseResource]) -> FlmServerResource:
        flm = resources.get("flm_server")
        if flm is None:
            raise KeyError("Missing required resource: flm_server")
        if not isinstance(flm, FlmServerResource):
            raise TypeError(
                "Resource `flm_server` must be an instance of FlmServerResource")
        return flm

    def _summarize(
        self,
        ctx: RunContext,
        flm: FlmServerResource,
        resources: dict[str, BaseResource],
        text: str,
        system_prompt: str,
    ) -> str:
        flm_response = flm.handle(
            ctx=ctx,
            request=FlmRequest(
                model=self.config.model,
                messages=[
                    FlmMessage(role="system", content=system_prompt),
                    FlmMessage(role="user", content=text),
                ],
            ),
            resources=resources,
        )
        return flm_response.content.strip()

    def handle(
        self,
        ctx: RunContext,
        request: SummarizeRequest,
        resources: dict[str, BaseResource],
    ) -> TextSummaryResult:
        flm = self._resolve_flm(resources)
        chunker = Chunker(self.config.chunk)
        chunks = chunker.chunk_text(request.text, request.file_hash)

        summ: dict[str, str] = {}
        for c in chunks:
            summ[c.hash] = self._summarize(ctx, flm, resources, c.text,
                                           _SUMMARIZE_SYSTEM_PROMPT)

        if len(chunks) <= 1:
            file_summary = summ[chunks[0].hash] if chunks else ""
        else:
            combined = "\n\n".join(
                f"Section {i + 1}: {summ[c.hash]}" for i, c in enumerate(chunks))
            file_summary = self._summarize(ctx, flm, resources, combined,
                                           _COMBINE_SYSTEM_PROMPT)

        documents, edges = chunks_to_multidoc(
            chunks,
            request.file_hash,
            SummaryChunk,
            file_cls=SummaryFile,
            per_chunk=lambda c: {"summary": summ[c.hash]},
            file_kwargs={"summary": file_summary},
        )

        return TextSummaryResult(
            documents=documents,
            edges=edges,
        )
