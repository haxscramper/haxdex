from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar

from haxdex_core.database import IndexerDatabase
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from pydantic import BaseModel, Field, model_serializer, model_validator
from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    select,
)

from haxdex_read_media.indexers.exif_metadata import EXIF_METADATA_TABLE


class XYObject(BaseModel, extra="allow"):
    f_0: float = Field(alias="0")
    f_1: float = Field(alias="1")


class Group(BaseModel, extra="allow"):
    title: str
    bounding: tuple[float, float, float, float]
    color: str | None = None
    font_size: float | None = None
    locked: bool | None = None


class ConfigModel(BaseModel, extra="allow"):
    links_ontop: bool | None = None
    align_to_grid: bool | None = None


class State(BaseModel, extra="allow"):
    lastGroupid: float | None = None
    lastNodeId: float | None = None
    lastLinkId: float | None = None
    lastRerouteId: float | None = None


class Flags(BaseModel, extra="allow"):
    collapsed: bool | None = None
    pinned: bool | None = None
    allow_interaction: bool | None = None
    horizontal: bool | None = None
    skip_repeated_outputs: bool | None = None


class Input(BaseModel, extra="allow"):
    name: str
    type: str | list[str] | float
    link: float | None = None
    slot_index: int | str | None = None


class Output(BaseModel, extra="allow"):
    name: str
    type: str | list[str] | float
    links: list[float] | None = None
    slot_index: int | str | None = None


class NodeProperties(BaseModel, extra="allow"):
    node_name_for_s_r: str | None = Field(
        default=None,
        alias="Node name for S&R",
    )


class Node(BaseModel, extra="allow"):
    id: int | str
    type: str
    title: str | None = None
    pos: XYObject | tuple[float, float]
    size: XYObject | tuple[float, float]
    flags: Flags
    order: float
    mode: float
    inputs: list[Input] | None = None
    outputs: list[Output] | None = None
    properties: NodeProperties
    widgets_values: list[Any] | dict[str, Any] | None = None
    color: str | None = None
    bgcolor: str | None = None


class Link(BaseModel, extra="allow"):
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


class Reroute(BaseModel, extra="allow"):
    id: float
    parentId: float | None = None
    pos: XYObject | tuple[float, float]
    linkIds: list[float] | None = None


class DS(BaseModel, extra="allow"):
    scale: float
    offset: XYObject | tuple[float, float]


class Info(BaseModel, extra="allow"):
    name: str
    author: str
    description: str
    version: str
    created: str
    modified: str
    software: str


class LinkExtension(BaseModel, extra="allow"):
    id: float
    parentId: float


class Extra(BaseModel, extra="allow"):
    ds: DS | None = None
    info: Info | None = None
    linkExtensions: list[LinkExtension] | None = None
    reroutes: list[Reroute] | None = None


class ModelItem(BaseModel, extra="allow"):
    name: str
    url: str
    hash: str | None = None
    hash_type: str | None = None
    directory: str


class ComfyWorkflow1_0(BaseModel, extra="allow"):
    version: float
    config: ConfigModel | None = None
    state: State = Field(default_factory=State)
    groups: list[Group] | None = None
    nodes: list[Node]
    links: list[Link] | None = None
    reroutes: list[Reroute] | None = None
    extra: Extra | None = None
    models: list[ModelItem] | None = None


class ComfyInputLink(BaseModel, extra="forbid"):
    origin_slot: int
    origin_id: str
    target_slot: int


class ComfyInput(BaseModel, extra="forbid"):
    node: str
    node_id: str
    title: str | None = None
    inputs: dict[str, Any] | list[Any]
    links: list[ComfyInputLink] = Field(
        description=("Incoming links to the node -- origin slot/id is a dependee node")
    )


COMFY_INPUT_SCHEMA = MetaData()

COMFY_INPUT_TABLE = Table(
    "comfy_input",
    COMFY_INPUT_SCHEMA,
    Column("hash", String(64), primary_key=True),
    Column("note", String, nullable=False),
)

COMFY_INPUT_NODES_TABLE = Table(
    "comfy_input_nodes",
    COMFY_INPUT_SCHEMA,
    Column(
        "file_hash",
        String(64),
        ForeignKey("comfy_input.hash"),
        primary_key=True,
    ),
    Column("node_id", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("node", String, nullable=False),
    Column("title", String),
    Column("inputs", JSON, nullable=False),
)

COMFY_INPUT_LINKS_TABLE = Table(
    "comfy_input_links",
    COMFY_INPUT_SCHEMA,
    Column(
        "file_hash",
        String(64),
        ForeignKey("comfy_input.hash"),
        primary_key=True,
    ),
    Column("target_id", String, primary_key=True),
    Column(
        "position",
        Integer,
        primary_key=True,
        autoincrement=False,
    ),
    Column("origin_id", String, nullable=False),
    Column("origin_slot", Integer, nullable=False),
    Column("target_slot", Integer, nullable=False),
)


def _workflow_inputs(workflow: ComfyWorkflow1_0) -> list[ComfyInput]:
    links_by_target: dict[str, list[ComfyInputLink]] = defaultdict(list)

    for link in workflow.links or ():
        links_by_target[str(link.target_id)].append(
            ComfyInputLink(
                origin_id=str(link.origin_id),
                origin_slot=int(link.origin_slot),
                target_slot=int(link.target_slot),
            )
        )

    result: list[ComfyInput] = []

    for node in workflow.nodes:
        if not node.widgets_values:
            continue

        title = node.title

        if title is None:
            metadata = (node.model_extra or {}).get("_meta", {})
            title = metadata.get("title")

        node_id = str(node.id)

        result.append(
            ComfyInput(
                node=node.type,
                node_id=node_id,
                title=title,
                inputs=node.widgets_values,
                links=links_by_target.get(node_id, []),
            )
        )

    return result


def _load_workflow(
    database: IndexerDatabase,
    file_hash: str,
) -> ComfyWorkflow1_0 | None:
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
        return ComfyWorkflow1_0.model_validate_json(workflow_data)

    return ComfyWorkflow1_0.model_validate(workflow_data)


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
        assets: dict[str, IndexerDatabase],
    ) -> None:
        file_hash = request.get_hash_str()

        if self.database.has_file(file_hash):
            return

        workflow = _load_workflow(
            assets["exif_metadata"],
            file_hash,
        )

        if workflow is None:
            inputs: list[ComfyInput] = []
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
                select(COMFY_INPUT_TABLE.c.hash).where(
                    COMFY_INPUT_TABLE.c.hash == file_hash
                )
            ).first()

            if existing is not None:
                return

            connection.execute(
                COMFY_INPUT_TABLE.insert(),
                {
                    "hash": file_hash,
                    "note": note,
                },
            )

            if node_rows:
                connection.execute(
                    COMFY_INPUT_NODES_TABLE.insert(),
                    node_rows,
                )

            if link_rows:
                connection.execute(
                    COMFY_INPUT_LINKS_TABLE.insert(),
                    link_rows,
                )
