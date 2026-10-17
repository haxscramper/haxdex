from __future__ import annotations

import base64
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, TypeAlias

import magic
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import (
    BaseIndexer,
    BaseIndexerConfig,
    BaseResource,
    RunContext,
)
from haxdex_core.types import IndexerRequest
from PIL import ExifTags, Image, UnidentifiedImageError
from PIL.TiffImagePlugin import IFDRational
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    select,
)

RX_DELETE = re.compile(r"[- _,()/\\:]")


class LoRATag(BaseModel, extra="forbid"):
    id2: str
    id3: str | None
    weight: str | float


def try_float(value: str) -> float | str:
    try:
        return float(value)
    except ValueError:
        return value


def _parse_embedded_text(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _decode_gps_info(gps_info: Any) -> Any:
    if not isinstance(gps_info, dict):
        return gps_info

    return {
        ExifTags.GPSTAGS.get(key, f"GPS_{key}"): value
        for key, value in gps_info.items()
    }


def _collect_all_embedded_metadata(img: Image.Image) -> dict[str, Any]:
    info_parsed = {
        key: (_parse_embedded_text(value) if isinstance(value, str) else value)
        for key, value in img.info.items()
    }

    exif = img.getexif()
    exif_by_id: dict[str, Any] = {}
    exif_by_name: dict[str, Any] = {}
    gps_info_by_name: Any = {}

    for tag_id, value in exif.items():
        tag_name = ExifTags.TAGS.get(tag_id, f"Tag_{tag_id}")
        exif_by_id[str(tag_id)] = value
        exif_by_name[tag_name] = value

        if tag_name == "GPSInfo" and isinstance(value, dict):
            gps_info_by_name = _decode_gps_info(value)

    for ifd_name, ifd_id in {
        "ExifIFD": 0x8769,
        "GPSInfoIFD": 0x8825,
        "InteropIFD": 0xA005,
    }.items():
        ifd_data = exif.get_ifd(ifd_id)
        exif_by_name[ifd_name] = ifd_data

        if ifd_name == "GPSInfoIFD" and not gps_info_by_name:
            gps_info_by_name = _decode_gps_info(ifd_data)

    return {
        "image": {
            "format": img.format,
            "format_description": img.format_description,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "size": [img.width, img.height],
            "is_animated": bool(getattr(img, "is_animated", False)),
            "n_frames": int(getattr(img, "n_frames", 1)),
        },
        "info_raw": dict(img.info),
        "info_parsed": info_parsed,
        "exif": {
            "by_tag_id": exif_by_id,
            "by_tag_name": exif_by_name,
            "gps_by_name": gps_info_by_name,
        },
    }


def _metadata_json_value(value: Any) -> Any:
    """Encode non-JSON Pillow values without silently stringifying them."""
    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        if math.isfinite(value):
            return value

        return {
            "__type__": "float",
            "value": repr(value),
        }

    if isinstance(value, IFDRational):
        return {
            "__type__": "rational",
            "numerator": value.numerator,
            "denominator": value.denominator,
        }

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "__type__": "bytes",
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }

    if isinstance(value, (list, tuple)):
        return [_metadata_json_value(item) for item in value]

    if isinstance(value, dict):
        result: dict[str, Any] = {}

        for key, item in value.items():
            if not isinstance(key, (str, int)):
                raise TypeError(f"Unsupported metadata key type: {type(key).__name__}")

            json_key = str(key)
            if json_key in result:
                raise ValueError(
                    f"Metadata keys collide after JSON conversion: {json_key!r}"
                )

            result[json_key] = _metadata_json_value(item)

        return result

    raise TypeError(f"Unsupported metadata value type: {type(value).__name__}")


class Tag(BaseModel, extra="forbid"):
    text: str
    amplifier: float = 1.0
    parens: int = 0

    def __repr__(self) -> str:
        if self.amplifier == 1.0:
            result = self.text
        else:
            result = f"{self.text}:{self.amplifier}"

        return "<{}{}{}{}>".format(
            "(" * self.parens,
            result,
            ")" * self.parens,
            ":",
        )


ParsedTag: TypeAlias = Tag | LoRATag | str


class ModelParam(BaseModel, extra="forbid"):
    name: str = ""
    weight: float = 1.0


class ImageTags(BaseModel, extra="forbid"):
    pass


class ImageParams(BaseModel, extra="forbid"):
    original_path: Path
    reference_dir: Path
    prompt: str = ""
    negative_prompt: str = ""
    generation_data: str = ""
    loras: list[ModelParam] = Field(default_factory=list)
    model: str = ""
    size: tuple[int, int] = (896, 1088)
    ImagePath: Path | None = None
    tags: ImageTags = Field(default_factory=ImageTags)
    parsed_prompt: list[ParsedTag] | None = None
    parsed_negative_prompt: list[ParsedTag] | None = None
    group_key: str = ""
    in_group_index: int = 0
    image_time: datetime = Field(default_factory=datetime.now)
    sampler: str = "Euler a"
    steps: int = 25
    cfgScale: float = 7.0
    clipSkip: int = 2
    sdVae: str = "Automatic"
    etaNoiseSeedDelta: int = 31337
    original_metadata_full: dict[str, Any] = Field(default_factory=dict)

    def get_tag_formatted_prompt(self) -> str:
        assert self.parsed_prompt is not None

        return ",".join(tag.text for tag in self.parsed_prompt if isinstance(tag, Tag))

    def get_tag_formatted_negative_prompt(self) -> str:
        assert self.parsed_negative_prompt is not None

        return ",".join(
            tag.text for tag in self.parsed_negative_prompt if isinstance(tag, Tag)
        )

    def get_tensor_art_prompt(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "negativePrompt": self.negative_prompt,
            "width": self.size[0],
            "height": self.size[1],
            "samplerName": self.sampler,
            "steps": self.steps,
            "cfgScale": self.cfgScale,
            "clipSkip": self.clipSkip,
            "sdVae": self.sdVae,
            "etaNoiseSeedDelta": self.etaNoiseSeedDelta,
        }

    def get_normalized_a1111(self) -> str:
        return f"""
{self.get_tag_formatted_prompt()}
Negative prompt: {self.get_tag_formatted_negative_prompt()},
Steps: {self.steps}, Model: {self.model}
"""


PATTERN = re.compile(
    "("
    + "|".join(
        re.escape(key)
        for key in [
            "Prompt",
            "Negative prompt",
            "Seed",
            "Size",
            "VAE",
            "Denoising strength",
            "Steps",
            "Sampler",
            "KSampler",
            "Schedule",
            "CFG scale",
            "Clip skip",
            "Model",
            "LoRA",
            "Hires resize",
            "Hires steps",
            "Hires upscaler",
            "ADetailer model",
            "ADetailer negative prompt",
            "ADetailer confidence",
            "Guidance",
        ]
    )
    + "):"
)


class SDGenParams(BaseModel):
    Prompt: str
    NegativePrompt: str = Field(alias="Negative prompt")
    Seed: str
    Size: str
    Steps: str
    VAE: str
    Sampler: str
    CFGScale: str = Field(alias="CFG scale")
    DenoisingStrength: str = Field(alias="Denoising strength")
    KSampler: str
    Schedule: str
    ClipSkip: str = Field(alias="Clip skip")
    Model: str
    LoRA: str | None = None
    HiresResize: str | None = Field(
        alias="Hires resize",
        default=None,
    )
    HiresSteps: str | None = Field(
        alias="Hires steps",
        default=None,
    )
    HiresUpscaler: str | None = Field(
        alias="Hires upscaler",
        default=None,
    )


def parse_parameters(param_string: str) -> SDGenParams | None:
    matches = list(PATTERN.finditer(param_string))
    values: dict[str, str] = {}

    for index, match in enumerate(matches):
        next_start = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(param_string)
        )

        key = match.group(1)
        values[key] = param_string[match.end() : next_start].strip()

    if "Prompt" not in values:
        return None

    return SDGenParams.model_validate(values)


class TArtV1ModelInfo(BaseModel):
    label: str
    modelId: str
    modelFileId: str
    weight: float
    modelFileName: str
    baseModel: str
    hash: str


class TArtV1BaseModelInfo(BaseModel):
    label: str
    modelId: str
    modelFileId: str
    modelFileName: str
    baseModel: str
    hash: str


class TArtV1SDXLSettings(BaseModel):
    refiner: bool | None = None


class TArtV1MainData(BaseModel):
    models: list[TArtV1ModelInfo] = Field(default_factory=list)
    prompt: str
    negativePrompt: str
    width: int
    height: int
    imageCount: int
    samplerName: str
    steps: int
    cfgScale: float
    seed: str | None = None
    clipSkip: int
    baseModel: TArtV1BaseModelInfo
    sdxl: TArtV1SDXLSettings | None = None
    workEngine: str = "TAMS_V2"
    sdVae: str = "Automatic"
    etaNoiseSeedDelta: int = 31337


class PromptParser:
    def __init__(self) -> None:
        self.break_token = "BREAK"

    def parse(self, prompt: str) -> list[ParsedTag]:
        return self.parse_tokens(self.tokenize(prompt))

    def tokenize(self, prompt: str) -> list[str]:
        prompt = re.sub(r"\s+", " ", prompt)
        prompt = re.sub(r"(,\s*)+", ",", prompt)

        return [
            token
            for token in re.split(
                r"(\n+|,|BREAK|<[^>]+>|[()])",
                prompt,
            )
            if token != "," and token.strip()
        ]

    def parse_tokens(self, tokens: list[str]) -> list[ParsedTag]:
        result: list[ParsedTag] = []
        current_level = 0

        def add_tag(
            text: str,
            amplifier: float = 1.0,
        ) -> None:
            result.append(
                Tag(
                    text=text.strip(),
                    amplifier=amplifier,
                    parens=current_level,
                )
            )

        for token in tokens:
            if not token:
                continue

            if token == "(":
                current_level += 1
            elif token == ")":
                current_level = max(0, current_level - 1)
            elif token == self.break_token:
                result.append(self.break_token)
            elif re.fullmatch(r"<[^>]+>", token):
                parts = token[1:-1].split(":")

                if len(parts) == 3:
                    result.append(
                        LoRATag(
                            id2=parts[0],
                            id3=parts[1],
                            weight=try_float(parts[2]),
                        )
                    )
                elif len(parts) == 2:
                    result.append(
                        LoRATag(
                            id2=parts[0],
                            id3=None,
                            weight=try_float(parts[1]),
                        )
                    )
            elif ":" in token:
                text, amplifier, *_ = token.split(":")
                text = text.strip()

                if text:
                    parsed_amplifier = try_float(amplifier)
                    add_tag(
                        text,
                        (
                            parsed_amplifier
                            if isinstance(parsed_amplifier, float)
                            else 1.0
                        ),
                    )
            else:
                add_tag(token)

        return result


def get_image_params(
    path: Path,
    reference_dir: Path,
    fast: bool = False,
) -> ImageParams:
    json_path = path.with_suffix(".json")

    result = ImageParams(
        original_path=path,
        reference_dir=reference_dir,
        image_time=datetime.fromtimestamp(path.stat().st_mtime),
    )

    if fast and not json_path.exists():
        return result

    sidecar_payload: dict[str, Any] = {}

    with Image.open(path) as img:
        result.ImagePath = path
        result.size = img.size

        if json_path.exists():
            sidecar_payload = json.loads(json_path.read_text())
            metadata = sidecar_payload.get("exif_metadata", {})
            json_metadata = True
        else:
            metadata = dict(img.info)
            json_metadata = False

        embedded = _collect_all_embedded_metadata(img)
        result.original_metadata_full = {
            "embedded": embedded,
            "sidecar": sidecar_payload,
            "exif_metadata": (metadata if json_metadata else embedded["info_parsed"]),
        }

    text_path = path.with_suffix(".txt")

    if text_path.exists():
        generation_data = text_path.read_text()
        parameters = parse_parameters(generation_data)

        if parameters is not None:
            result.prompt = parameters.Prompt.lstrip(",")
            result.negative_prompt = parameters.NegativePrompt.lstrip(",")
            result.generation_data = generation_data
            result.sampler = parameters.Sampler.strip(",")
            result.steps = int(parameters.Steps.strip(","))
            result.model = parameters.Model.strip(",")
            result.clipSkip = int(parameters.ClipSkip.strip(","))
            result.cfgScale = float(parameters.CFGScale.strip(","))

            width, height = parameters.Size.strip(",").split("x")
            result.size = (int(width), int(height))

            if parameters.LoRA:
                for value in parameters.LoRA.split(","):
                    value = value.strip()
                    if not value:
                        continue

                    if ":" in value:
                        name, weight = value.rsplit(":", 1)
                        model_weight = float(weight)
                    else:
                        name = value
                        model_weight = 1.0

                    result.loras.append(
                        ModelParam(
                            name=name,
                            weight=model_weight,
                        )
                    )

    elif "generation_data" in metadata:
        if json_metadata:
            generation_json = metadata["generation_data"]
        else:
            raw = metadata["generation_data"]
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")

            generation_json = json.loads(str(raw).strip("\x00"))

        generation = TArtV1MainData.model_validate(generation_json)

        result.prompt = generation.prompt
        result.negative_prompt = generation.negativePrompt
        result.generation_data = json.dumps(generation_json)
        result.sampler = generation.samplerName
        result.steps = generation.steps
        result.model = generation.baseModel.modelFileName
        result.clipSkip = generation.clipSkip
        result.cfgScale = generation.cfgScale
        result.size = (generation.width, generation.height)
        result.sdVae = generation.sdVae
        result.etaNoiseSeedDelta = generation.etaNoiseSeedDelta
        result.loras = [
            ModelParam(
                name=model.modelFileName,
                weight=model.weight,
            )
            for model in generation.models
        ]

    elif "prompt" in metadata:
        if json_metadata:
            prompt = metadata["prompt"]
        else:
            raw = metadata["prompt"]
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")

            prompt = json.loads(raw)

        for node in prompt.values():
            if node["class_type"] != "BNK_CLIPTextEncodeAdvanced":
                continue

            text = node["inputs"]["text"]
            if "EasyNegative" in text or "bad anatomy" in text or "negative" in text:
                result.negative_prompt = text
            else:
                result.prompt = text

    parser = PromptParser()

    if result.prompt:
        result.parsed_prompt = parser.parse(result.prompt)

    if result.negative_prompt:
        result.parsed_negative_prompt = parser.parse(result.negative_prompt)

    return result


EXIF_METADATA_SCHEMA = MetaData()

EXIF_METADATA_TABLE = Table(
    "exif_metadata",
    EXIF_METADATA_SCHEMA,
    Column("hash", String(64), primary_key=True),
    Column("error", String),
    Column("original_path", String),
    Column("reference_dir", String),
    Column("image_path", String),
    Column("prompt", String),
    Column("negative_prompt", String),
    Column("generation_data", String),
    Column("model", String),
    Column("width", Integer),
    Column("height", Integer),
    Column("group_key", String),
    Column("in_group_index", Integer),
    Column("image_time", DateTime),
    Column("sampler", String),
    Column("steps", Integer),
    Column("cfg_scale", Float),
    Column("clip_skip", Integer),
    Column("sd_vae", String),
    Column("eta_noise_seed_delta", Integer),
    Column("parsed_prompt_present", Boolean),
    Column("parsed_negative_prompt_present", Boolean),
    Column("original_metadata_full", JSON),
)

EXIF_METADATA_LORAS_TABLE = Table(
    "exif_metadata_loras",
    EXIF_METADATA_SCHEMA,
    Column(
        "file_hash",
        String(64),
        ForeignKey("exif_metadata.hash"),
        primary_key=True,
    ),
    Column(
        "position",
        Integer,
        primary_key=True,
        autoincrement=False,
    ),
    Column("name", String, nullable=False),
    Column("weight", Float, nullable=False),
)

EXIF_METADATA_PROMPT_TAGS_TABLE = Table(
    "exif_metadata_prompt_tags",
    EXIF_METADATA_SCHEMA,
    Column(
        "file_hash",
        String(64),
        ForeignKey("exif_metadata.hash"),
        primary_key=True,
    ),
    Column("polarity", String, primary_key=True),
    Column(
        "position",
        Integer,
        primary_key=True,
        autoincrement=False,
    ),
    Column("kind", String, nullable=False),
    Column("text", String),
    Column("amplifier", Float),
    Column("parens", Integer),
    Column("id2", String),
    Column("id3", String),
    Column("weight_float", Float),
    Column("weight_text", String),
)


def _image_params_row(
    file_hash: str,
    params: ImageParams,
) -> dict[str, Any]:
    return {
        "hash": file_hash,
        "error": None,
        "original_path": str(params.original_path),
        "reference_dir": str(params.reference_dir),
        "image_path": (str(params.ImagePath) if params.ImagePath is not None else None),
        "prompt": params.prompt,
        "negative_prompt": params.negative_prompt,
        "generation_data": params.generation_data,
        "model": params.model,
        "width": params.size[0],
        "height": params.size[1],
        "group_key": params.group_key,
        "in_group_index": params.in_group_index,
        "image_time": params.image_time,
        "sampler": params.sampler,
        "steps": params.steps,
        "cfg_scale": params.cfgScale,
        "clip_skip": params.clipSkip,
        "sd_vae": params.sdVae,
        "eta_noise_seed_delta": params.etaNoiseSeedDelta,
        "parsed_prompt_present": params.parsed_prompt is not None,
        "parsed_negative_prompt_present": (params.parsed_negative_prompt is not None),
        "original_metadata_full": _metadata_json_value(params.original_metadata_full),
    }


def _prompt_tag_rows(
    file_hash: str,
    params: ImageParams,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for polarity, tags in (
        ("positive", params.parsed_prompt),
        ("negative", params.parsed_negative_prompt),
    ):
        if tags is None:
            continue

        for position, tag in enumerate(tags):
            row: dict[str, Any] = {
                "file_hash": file_hash,
                "polarity": polarity,
                "position": position,
                "kind": None,
                "text": None,
                "amplifier": None,
                "parens": None,
                "id2": None,
                "id3": None,
                "weight_float": None,
                "weight_text": None,
            }

            if isinstance(tag, Tag):
                row.update(
                    kind="tag",
                    text=tag.text,
                    amplifier=tag.amplifier,
                    parens=tag.parens,
                )
            elif isinstance(tag, LoRATag):
                row.update(
                    kind="lora",
                    id2=tag.id2,
                    id3=tag.id3,
                )

                if isinstance(tag.weight, str):
                    row["weight_text"] = tag.weight
                else:
                    row["weight_float"] = tag.weight
            else:
                row.update(
                    kind="text",
                    text=tag,
                )

            rows.append(row)

    return rows


class ExifMetadataIndexer(BaseIndexer):
    asset_name = "exif_metadata"
    result_schema: ClassVar[MetaData] = EXIF_METADATA_SCHEMA
    max_parallel = 8

    def __init__(
        self,
        config: BaseIndexerConfig,
        database: IndexerStore,
    ) -> None:
        super().__init__(config=config, database=database)
        self._magic = magic.Magic(mime=True)

    def can_run(self, path: Path) -> bool:
        return self._magic.from_file(str(path.absolute())) in {
            "image/png",
            "image/avif",
            "image/heic",
            "image/heif",
            "image/webp",
            "image/tiff",
            "image/jpeg",
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

        path = ctx.get_path(request.file_ref)
        params: ImageParams | None = None
        error: str | None = None

        try:
            params = get_image_params(path, path.parent)
        except UnidentifiedImageError as exception:
            error = str(exception)
        except OSError as exception:
            if "image file is truncated" not in str(exception):
                raise
            error = str(exception)

        if params is None:
            document_row = {
                "hash": file_hash,
                "error": error,
            }
            lora_rows: list[dict[str, Any]] = []
            prompt_rows: list[dict[str, Any]] = []
        else:
            document_row = _image_params_row(file_hash, params)
            lora_rows = [
                {
                    "file_hash": file_hash,
                    "position": position,
                    "name": model.name,
                    "weight": model.weight,
                }
                for position, model in enumerate(params.loras)
            ]
            prompt_rows = _prompt_tag_rows(file_hash, params)

        with self.database.transaction() as connection:
            existing = connection.execute(
                select(EXIF_METADATA_TABLE.c.hash).where(
                    EXIF_METADATA_TABLE.c.hash == file_hash
                )
            ).first()

            if existing is not None:
                return

            connection.execute(
                EXIF_METADATA_TABLE.insert(),
                document_row,
            )

            if lora_rows:
                connection.execute(
                    EXIF_METADATA_LORAS_TABLE.insert(),
                    lora_rows,
                )

            if prompt_rows:
                connection.execute(
                    EXIF_METADATA_PROMPT_TAGS_TABLE.insert(),
                    prompt_rows,
                )
