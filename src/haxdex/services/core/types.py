from __future__ import annotations

from pathlib import Path

from beartype import beartype
from beartype.typing import Annotated, Any, ClassVar, Literal, Optional, Union
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    TypeAdapter,
)

AnyModel = Annotated[
    BaseModel,
    PlainValidator(lambda v: v),
    PlainSerializer(lambda v: v.model_dump(), return_type=dict),
]


class IndexEdge(BaseModel, extra="forbid"):
    file_hash: str
    from_: str
    to_: str


class VectorIndexConfig(BaseModel, extra="forbid"):
    index_path: str
    vector_dimensions: int
    vector_metric: str = "cosine"
    n_lists: int = 1
    sparse: bool = True


class FullTextIndexConfig(BaseModel, extra="forbid"):
    index_path: str
    analyzer: str = "text_en"
    bm25: bool = True


class IndexDocument(BaseModel, extra="forbid"):
    kind: Literal["processed"] = "processed"
    hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="[0-9A-Fa-f]{64}",
    )
    vector_index: ClassVar[Optional[VectorIndexConfig]] = None
    full_text_index: ClassVar[Optional[FullTextIndexConfig]] = None


class IndexMultiDocument(IndexDocument, extra="forbid"):
    file_hash: str = Field(
        description=
        "Hash of the original file -- `.hash` field in all cases refers to *this* "
        "specific document, but if the query needs to re-assemble the information "
        "extracted from the file, grouping by `file_hash` is the way to go. ")


class MultiDocumentModel(BaseModel, extra="forbid"):
    kind: Literal["processed"] = "processed"
    edge_type: ClassVar[Any]
    document_type: ClassVar[Any]

    edges: list[IndexEdge]
    documents: list[IndexMultiDocument]


class MissingAssets(BaseModel, extra="forbid"):
    kind: Literal["missing_assets"] = "missing_assets"
    missing_assets: list[str] = Field(default_factory=list)
    description: str = ""


class CannotProcess(BaseModel, extra="forbid"):
    kind: Literal["cannot_process"] = "cannot_process"
    reason: str


IndexerResultValue = Annotated[
    Union[IndexDocument, MissingAssets, CannotProcess],
    Field(discriminator="kind"),
]
"Discriminated alias used to parse serialized indexer results. Concrete "
"'processed' payloads are validated against the indexer's own result model."

_result_adapter: TypeAdapter[Any] = TypeAdapter(IndexerResultValue)


def is_processed_result(result: BaseModel) -> bool:
    return getattr(result, "kind", None) == "processed"


def parse_indexer_result(result_model: type[BaseModel], data: Any) -> BaseModel:
    """Parse a serialized `IndexerOutput.result` payload.

    The concrete type of a 'processed' payload is only known to the indexer
    itself, so it is validated against `result_model`; error categories are
    parsed via the discriminated alias.
    """
    if isinstance(data, dict) and data.get("kind") == "processed":
        return result_model.model_validate(data)

    return _result_adapter.validate_python(data)


@beartype
class FileHash(BaseModel, extra="forbid"):
    model_config = ConfigDict(frozen=True)
    hash: str

    def __repr__(self) -> str:
        return self.hash


@beartype
class RootRef(BaseModel, extra="forbid"):
    model_config = ConfigDict(frozen=True)
    name: str


@beartype
class FileRef(BaseModel, extra="forbid"):
    model_config = ConfigDict(frozen=True)
    hash: FileHash
    relative: str
    root: RootRef


@beartype
class IndexerOutput(BaseModel, extra="forbid"):
    model_config = ConfigDict(frozen=True)
    indexer_id: str
    result: AnyModel | MissingAssets | CannotProcess


@beartype
class IndexerRequest(BaseModel, extra="forbid"):
    file_ref: FileRef
    dependency_results: dict[str, IndexerOutput | None] = Field(default_factory=dict)

    def get_hash_str(self) -> str:
        return self.file_ref.hash.hash
