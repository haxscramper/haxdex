from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar

from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from pydantic import BaseModel, Field, model_serializer, model_validator
from sqlalchemy import (
    JSON,
    ForeignKey,
    MetaData,
    String,
    insert,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from haxdex_read_media.indexers.exif_metadata import EXIF_METADATA_TABLE


class XYObjectObject(BaseModel, extra="allow"):
    f_0: float = Field(alias="0")
    f_1: float = Field(alias="1")


class GroupObject(BaseModel, extra="allow"):
    title: str
    bounding: tuple[float, float, float, float]
    color: str | None = None
    font_size: float | None = None
    locked: bool | None = None


class ConfigModelObject(BaseModel, extra="allow"):
    links_ontop: bool | None = None
    align_to_grid: bool | None = None


class StateObject(BaseModel, extra="allow"):
    lastGroupid: float | None = None
    lastNodeId: float | None = None
    lastLinkId: float | None = None
    lastRerouteId: float | None = None


class FlagsObject(BaseModel, extra="allow"):
    collapsed: bool | None = None
    pinned: bool | None = None
    allow_interaction: bool | None = None
    horizontal: bool | None = None
    skip_repeated_outputs: bool | None = None


class InputObject(BaseModel, extra="allow"):
    name: str
    type: str | list[str] | float
    link: float | None = None
    slot_index: int | str | None = None


class OutputObject(BaseModel, extra="allow"):
    name: str
    type: str | list[str] | float
    links: list[float] | None = None
    slot_index: int | str | None = None


class NodePropertiesObject(BaseModel, extra="allow"):
    node_name_for_s_r: str | None = Field(
        default=None,
        alias="Node name for S&R",
    )


class NodeObject(BaseModel, extra="allow"):
    id: int | str
    type: str
    title: str | None = None
    pos: XYObjectObject | tuple[float, float]
    size: XYObjectObject | tuple[float, float]
    flags: FlagsObject
    order: float
    mode: float
    inputs: list[InputObject] | None = None
    outputs: list[OutputObject] | None = None
    properties: NodePropertiesObject
    widgets_values: list[Any] | dict[str, Any] | None = None
    color: str | None = None
    bgcolor: str | None = None


class LinkObject(BaseModel, extra="allow"):
    id: float
    origin_id: int | str
    origin_slot: int | str
    target_id: int | str
    target_slot: int | str
    type: str | list[str] | float
    parentId: float | None = None

    _ARRAY_FIELDS: ClassVar[tuple[str, ...]] = (
        "id",
        "origin_id",
        "origin_slot",
        "target_id",
        "target_slot",
        "type",
    )

    @model_validator(mode="before")
    @classmethod
    def _from_array(cls, data: Any) -> Any:
        if not isinstance(data, (list, tuple)):
            return data

        result = dict(zip(cls._ARRAY_FIELDS, data))

        if len(data) > len(cls._ARRAY_FIELDS):
            result["parentId"] = data[len(cls._ARRAY_FIELDS)]

        return result

    @model_serializer
    def _to_array(self) -> list[Any]:
        result = [getattr(self, field) for field in self._ARRAY_FIELDS]

        if self.parentId is not None:
            result.append(self.parentId)

        return result


class RerouteObject(BaseModel, extra="allow"):
    id: float
    parentId: float | None = None
    pos: XYObjectObject | tuple[float, float]
    linkIds: list[float] | None = None


class DSObject(BaseModel, extra="allow"):
    scale: float
    offset: XYObjectObject | tuple[float, float]


class InfoObject(BaseModel, extra="allow"):
    name: str
    author: str
    description: str
    version: str
    created: str
    modified: str
    software: str


class LinkExtensionObject(BaseModel, extra="allow"):
    id: float
    parentId: float


class ExtraObject(BaseModel, extra="allow"):
    ds: DSObject | None = None
    info: InfoObject | None = None
    linkExtensions: list[LinkExtensionObject] | None = None
    reroutes: list[RerouteObject] | None = None


class ModelItemObject(BaseModel, extra="allow"):
    name: str
    url: str
    hash: str | None = None
    hash_type: str | None = None
    directory: str


class ComfyWorkflow1_0Object(BaseModel, extra="allow"):
    version: float
    config: ConfigModelObject | None = None
    state: StateObject = Field(default_factory=StateObject)
    groups: list[GroupObject] | None = None
    nodes: list[NodeObject]
    links: list[LinkObject] | None = None
    reroutes: list[RerouteObject] | None = None
    extra: ExtraObject | None = None
    models: list[ModelItemObject] | None = None


class ComfyInputLinkObject(BaseModel, extra="forbid"):
    origin_slot: int
    origin_id: str
    target_slot: int


class ComfyInputObject(BaseModel, extra="forbid"):
    node: str
    node_id: str
    title: str | None = None
    inputs: dict[str, Any] | list[Any]
    links: list[ComfyInputLinkObject] = Field(
        description=("Incoming links to the node -- origin slot/id is a dependee node")
    )


COMFY_INPUT_SCHEMA = MetaData()


class ComfyInputBase(DeclarativeBase):
    metadata = COMFY_INPUT_SCHEMA


class ComfyInput(ComfyInputBase):
    __tablename__ = "comfy_input"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    note: Mapped[str] = mapped_column(String, nullable=False)


class ComfyInputNode(ComfyInputBase):
    __tablename__ = "comfy_input_nodes"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("comfy_input.hash"),
        primary_key=True,
    )
    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(nullable=False)
    node: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    inputs: Mapped[Any] = mapped_column(JSON, nullable=False)


class ComfyInputLink(ComfyInputBase):
    __tablename__ = "comfy_input_links"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("comfy_input.hash"),
        primary_key=True,
    )
    target_id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=False,
    )
    origin_id: Mapped[str] = mapped_column(String, nullable=False)
    origin_slot: Mapped[int] = mapped_column(nullable=False)
    target_slot: Mapped[int] = mapped_column(nullable=False)


def _workflow_inputs(workflow: ComfyWorkflow1_0Object) -> list[ComfyInputObject]:
    links_by_target: dict[str, list[ComfyInputLinkObject]] = defaultdict(list)

    for link in workflow.links or ():
        links_by_target[str(link.target_id)].append(
            ComfyInputLinkObject(
                origin_id=str(link.origin_id),
                origin_slot=int(link.origin_slot),
                target_slot=int(link.target_slot),
            )
        )

    result: list[ComfyInputObject] = []

    for node in workflow.nodes:
        if not node.widgets_values:
            continue

        title = node.title

        if title is None:
            metadata = (node.model_extra or {}).get("_meta", {})
            title = metadata.get("title")

        node_id = str(node.id)

        result.append(
            ComfyInputObject(
                node=node.type,
                node_id=node_id,
                title=title,
                inputs=node.widgets_values,
                links=links_by_target.get(node_id, []),
            )
        )

    return result


def _load_workflow(
    database: IndexerStore,
    file_hash: str,
) -> ComfyWorkflow1_0Object | None:
    with database.connection() as connection:
        row = (
            connection.execute(
                select(
                    EXIF_METADATA_TABLE.c.error,
                    EXIF_METADATA_TABLE.c.original_metadata_full,
                ).where(EXIF_METADATA_TABLE.c.hash == file_hash)
            )
            .mappings()
            .one_or_none()
        )

    if row is None:
        raise LookupError(f"No EXIF metadata result for file {file_hash}")

    if row["error"] is not None:
        raise ValueError(
            f"Cannot extract ComfyUI workflow for file {file_hash}: "
            f"EXIF extraction failed: {row['error']}"
        )

    metadata = row["original_metadata_full"]
    if not isinstance(metadata, dict):
        raise TypeError(
            f"Expected original_metadata_full to be an object for {file_hash}"
        )

    exif_metadata = metadata.get("exif_metadata", {})
    if not isinstance(exif_metadata, dict):
        raise TypeError(f"Expected exif_metadata to be an object for {file_hash}")

    if "workflow" not in exif_metadata:
        return None

    workflow_data = exif_metadata["workflow"]

    if isinstance(workflow_data, str):
        return ComfyWorkflow1_0Object.model_validate_json(workflow_data)

    return ComfyWorkflow1_0Object.model_validate(workflow_data)


class ComfyInputIndexer(BaseIndexer):
    asset_name = "comfy_input"
    result_schema: ClassVar[MetaData] = COMFY_INPUT_SCHEMA
    required_assets = ("exif_metadata",)

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".png",
            ".jpg",
            ".webp",
            ".jpeg",
        }

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

        workflow = _load_workflow(
            assets["exif_metadata"],
            file_hash,
        )

        if workflow is None:
            inputs: list[ComfyInputObject] = []
            note = "no workflow data detected"
        else:
            inputs = _workflow_inputs(workflow)
            note = ""

        node_rows = [
            {
                "file_hash": file_hash,
                "node_id": node.node_id,
                "position": position,
                "node": node.node,
                "title": node.title,
                "inputs": node.inputs,
            }
            for position, node in enumerate(inputs)
        ]

        link_rows = [
            {
                "file_hash": file_hash,
                "target_id": node.node_id,
                "position": position,
                "origin_id": link.origin_id,
                "origin_slot": link.origin_slot,
                "target_slot": link.target_slot,
            }
            for node in inputs
            for position, link in enumerate(node.links)
        ]

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(ComfyInput.hash).where(ComfyInput.hash == file_hash)
            ).first()

            if existing is not None:
                return

            connection.execute(insert(ComfyInput).values(hash=file_hash, note=note))

            if node_rows:
                connection.execute(insert(ComfyInputNode), node_rows)

            if link_rows:
                connection.execute(insert(ComfyInputLink), link_rows)
