from dataclasses import dataclass
from pathlib import Path

from beartype import beartype
from beartype.typing import Any
from pydantic import BaseModel, Field

from haxdex.services.indexers.image_generation import GenerationParamsIndexerResult
from haxdex.services.indexers.safetensor_indexer import SafetensorIndexerResult
from haxdex.services.indexers.wd_indexer import WdTagIndexerResult


@beartype
class ExplorerSettings(BaseModel, extra="forbid"):
    comfy_dir: Path
    workflow_path: Path
    comfy_url: str = "http://127.0.0.1:8188"
    model_replacements: dict[str, str] = Field(default_factory=dict)
    arch_type_map: dict[str, str] = Field(default_factory=dict)
    model_arch_map: dict[str, str] = Field(default_factory=dict)
    default_vae: dict[str, str] = Field(default_factory=dict)
    default_clip: dict[str, str] = Field(default_factory=dict)
    replacement_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


@beartype
@dataclass(frozen=True)
class ModelEntry:
    display: str
    payload: str
    arch: str | None
    model_type: str | None


@beartype
@dataclass
class IndexedFileRecord:
    path: str
    file_hash: str
    root: str
    relative: str
    generation_params: GenerationParamsIndexerResult | None
    safetensor: SafetensorIndexerResult | None
    wd_tags: WdTagIndexerResult | None


@beartype
@dataclass
class LoraState:
    enabled: bool
    model: str
    weight: float


@beartype
@dataclass
class GenerationRowState:
    path: str
    md5: str
    indexer_id: str
    positive: str
    negative: str
    width: int
    height: int
    steps: int
    cfg: float
    checkpoint: str
    loras: list[LoraState]
    vae: str | None
    clip: str | None


@beartype
@dataclass
class ImageSearchRecord:
    path: str
    md5: str
    generation_params: GenerationParamsIndexerResult | None
    wd_tags: WdTagIndexerResult | None
    searchable_tags: set[str]
