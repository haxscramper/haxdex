import json
from pathlib import Path

from beartype import beartype
from beartype.typing import Any, Literal, Mapping, Optional
from haxdex_core.job_types import BaseIndexerConfig
from haxdex_core.utils import get_xdg_cache_dir
from haxdex_read_media.indexers.comfy_input_indexer import (
    ComfyInputIndexer,
)
from haxdex_read_media.indexers.exif_metadata import (
    ExifMetadataIndexer,
)
from haxdex_read_media.indexers.ffprobe_indexer import (
    FFProbeIndexer,
)
from haxdex_read_media.indexers.image_generation import (
    GenerationParamsIndexer,
)
from haxdex_read_media.indexers.image_hash import ImageHashIndexer
from haxdex_read_media.indexers.media_transcribe import (
    MediaTranscriptionIndexer,
)
from haxdex_read_media.indexers.mime_indexer import (
    FileMimeIndexer,
)
from haxdex_read_media.indexers.pdf_indexer import PdfIndexer
from haxdex_read_media.indexers.safetensor_indexer import (
    SafetensorIndexer,
)
from haxdex_read_media.indexers.wd_indexer import WdTagIndexer
from haxdex_read_media.resources.flm_server import (
    FlmServerResource,
)
from haxdex_read_media.resources.pdf.pdf_extractor import (
    PdfExtractor,
)
from haxdex_read_media.resources.wd_tagger import WdTagger
from haxdex_read_media.resources.whisper_transcribe import (
    WhisperTranscribeResource,
)
from haxdex_view_generation.models import ExplorerSettings
from hstd_py_lib.pydantic_utils import ExistingDir, ExistingPath, OutputFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from haxdex.services.default_job_types import (
    DEFAULT_INDEXER_TYPES,
    DEFAULT_RESOURCE_TYPES,
)
from haxdex.services.file_iteration import DirConfig
from haxdex.visual.trash_action_visual import TrashActionVisualConfig

_INDEXER_TYPES = [t for t in DEFAULT_INDEXER_TYPES] + [
    WdTagIndexer,
    PdfIndexer,
    FFProbeIndexer,
    SafetensorIndexer,
    GenerationParamsIndexer,
    ExifMetadataIndexer,
    ComfyInputIndexer,
    MediaTranscriptionIndexer,
    ImageHashIndexer,
    FileMimeIndexer,
]

_RESOURCE_TYPES = [t for t in DEFAULT_RESOURCE_TYPES] + [
    FlmServerResource,
    WdTagger,
    PdfExtractor,
    WhisperTranscribeResource,
]

_INDEXER_BY_NAME = {cls.asset_name: cls for cls in _INDEXER_TYPES}
_RESOURCE_BY_NAME = {cls.resource_key: cls for cls in _RESOURCE_TYPES}


@beartype
def _validate_plugin_config_map(
    raw: dict[str, Any],
    registry: dict[str, type],
    kind: str,
) -> dict[str, BaseModel]:
    validated: dict[str, BaseModel] = {}
    for name, cfg in raw.items():
        if name not in registry:
            raise ValueError(
                f"Unknown {kind} '{name}', expected names: {sorted(registry.keys())}"
            )

        cls = registry[name]
        config_model = getattr(cls, "config_model", None)
        if not isinstance(config_model, type) or not issubclass(
            config_model, BaseModel
        ):
            raise TypeError(f"{kind.capitalize()} '{name}' has invalid config_model")

        if cfg is None:
            cfg = {}
        if isinstance(cfg, dict):
            validated[name] = config_model.model_validate(cfg)

        elif isinstance(cfg, config_model):
            validated[name] = cfg

        else:
            raise TypeError(
                f"{kind.capitalize()} '{name}' config must be an object, got {type(cfg).__name__}"
            )

    return validated


class DatabaseConfig(BaseModel, extra="forbid"):
    directory: Path


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logfile: OutputFile | None = None
    logfile_format: Literal["text", "json"] = "text"  # type: ignore
    setup_handlers: bool = True


class IndexPathConfig(BaseModel, extra="forbid"):
    name: str
    root_path: ExistingDir
    paths: list[DirConfig]


class IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    paths: list[IndexPathConfig] = Field(default_factory=list)
    reset: bool = False
    limit_total: int | None = None
    limit_per_path: int | None = None
    max_plan_run_size: int = 256
    media_transcribe_whisper_server: ExistingPath | None = None


class VisualConfig(BaseModel, extra="forbid", frozen=True):
    trash: Optional[TrashActionVisualConfig] = None


class AppConfig(BaseModel, extra="forbid"):
    fuck_off__schema: str | None = Field(alias="$schema", default=None)

    db: DatabaseConfig
    hash_cache: OutputFile = Field(
        description="DB location for the file hash cache",
        default_factory=lambda: get_xdg_cache_dir([]).joinpath(
            "file_hash_cache.sqlite"
        ),
    )

    common_ignore_prefix: list[str] = Field(default_factory=list)

    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    generation_explorer: ExplorerSettings | None = None

    indexers: dict[str, BaseIndexerConfig] = Field(default_factory=dict, exclude=True)
    resources: dict[str, BaseModel] = Field(default_factory=dict, exclude=True)
    perf_trace_file: OutputFile | None = None

    # exactly one of these must be set
    index: IndexConfig | None = None
    visual: VisualConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _defaults_for_plugin_maps(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if "indexers" not in data:
            data["indexers"] = {name: {} for name in _INDEXER_BY_NAME}

        if "resources" not in data:
            data["resources"] = {name: {} for name in _RESOURCE_BY_NAME}

        return data

    @field_validator("indexers", mode="before")
    @classmethod
    def _validate_indexers_field(cls, value: Any) -> dict[str, BaseModel]:
        if value is None:
            value = {}

        if not isinstance(value, dict):
            raise TypeError(
                f"Indexers config must be an object, got {type(value).__name__}"
            )

        return _validate_plugin_config_map(
            raw=value,
            registry=_INDEXER_BY_NAME,
            kind="indexer",
        )

    @field_validator("resources", mode="before")
    @classmethod
    def _validate_resources_field(cls, value: Any) -> dict[str, BaseModel]:
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise TypeError(
                f"Resources config must be an object, got {type(value).__name__}"
            )
        return _validate_plugin_config_map(
            raw=value,
            registry=_RESOURCE_BY_NAME,
            kind="resource",
        )

    @model_validator(mode="after")
    def validate_full_config(self) -> "AppConfig":
        return self

    @classmethod
    def _append_common_ignore_prefix(cls, value: object, prefix: list[str]) -> None:
        if isinstance(value, DirConfig):
            value.ignore = [*prefix, *value.ignore]
            return

        if isinstance(value, BaseModel):
            for field_name in value.__class__.model_fields:
                cls._append_common_ignore_prefix(getattr(value, field_name), prefix)
            return

        if isinstance(value, Mapping):
            for item in value.values():
                cls._append_common_ignore_prefix(item, prefix)
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                cls._append_common_ignore_prefix(item, prefix)
