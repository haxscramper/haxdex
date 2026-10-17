from __future__ import annotations

import base64
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

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
from sqlalchemy import JSON, ForeignKey, MetaData, String, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    Session,
    mapped_column,
    relationship,
)

EXIF_METADATA_SCHEMA = MetaData()


class ExifMetadataBase(MappedAsDataclass, DeclarativeBase, kw_only=True):
    metadata = EXIF_METADATA_SCHEMA


class ExifMetadata(ExifMetadataBase):
    __tablename__ = "exif_metadata"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    error: Mapped[str | None] = mapped_column(default=None)
    original_path: Mapped[str | None] = mapped_column(default=None)
    reference_dir: Mapped[str | None] = mapped_column(default=None)
    image_path: Mapped[str | None] = mapped_column(default=None)
    prompt: Mapped[str] = mapped_column(default="")
    negative_prompt: Mapped[str] = mapped_column(default="")
    generation_data: Mapped[str] = mapped_column(default="")
    model: Mapped[str] = mapped_column(default="")
    width: Mapped[int] = mapped_column(default=896)
    height: Mapped[int] = mapped_column(default=1088)
    group_key: Mapped[str] = mapped_column(default="")
    in_group_index: Mapped[int] = mapped_column(default=0)
    image_time: Mapped[datetime] = mapped_column(default_factory=datetime.now)
    sampler: Mapped[str] = mapped_column(default="Euler a")
    steps: Mapped[int] = mapped_column(default=25)
    cfg_scale: Mapped[float] = mapped_column(default=7.0)
    clip_skip: Mapped[int] = mapped_column(default=2)
    sd_vae: Mapped[str] = mapped_column(default="Automatic")
    eta_noise_seed_delta: Mapped[int] = mapped_column(default=31337)
    parsed_prompt_present: Mapped[bool] = mapped_column(default=False)
    parsed_negative_prompt_present: Mapped[bool] = mapped_column(default=False)
    original_metadata_full: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default_factory=dict,
    )

    loras: Mapped[list[ExifMetadataLora]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ExifMetadataLora.position",
        default_factory=list,
        repr=False,
    )
    prompt_tags: Mapped[list[ExifMetadataPromptTag]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by=("ExifMetadataPromptTag.polarity, ExifMetadataPromptTag.position"),
        default_factory=list,
        repr=False,
    )

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @size.setter
    def size(self, value: tuple[int, int]) -> None:
        self.width, self.height = value

    @property
    def parsed_prompt(self) -> list[ExifMetadataPromptTag] | None:
        if not self.parsed_prompt_present:
            return None

        return [tag for tag in self.prompt_tags if tag.polarity == "positive"]

    @property
    def parsed_negative_prompt(self) -> list[ExifMetadataPromptTag] | None:
        if not self.parsed_negative_prompt_present:
            return None

        return [tag for tag in self.prompt_tags if tag.polarity == "negative"]

    def get_tag_formatted_prompt(self) -> str:
        tags = self.parsed_prompt
        assert tags is not None

        return ",".join(
            tag.text for tag in tags if tag.kind == "tag" and tag.text is not None
        )

    def get_tag_formatted_negative_prompt(self) -> str:
        tags = self.parsed_negative_prompt
        assert tags is not None

        return ",".join(
            tag.text for tag in tags if tag.kind == "tag" and tag.text is not None
        )

    def get_tensor_art_prompt(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "negativePrompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "samplerName": self.sampler,
            "steps": self.steps,
            "cfgScale": self.cfg_scale,
            "clipSkip": self.clip_skip,
            "sdVae": self.sd_vae,
            "etaNoiseSeedDelta": self.eta_noise_seed_delta,
        }

    def get_normalized_a1111(self) -> str:
        return f"""
{self.get_tag_formatted_prompt()}
Negative prompt: {self.get_tag_formatted_negative_prompt()},
Steps: {self.steps}, Model: {self.model}
"""


class ExifMetadataLora(ExifMetadataBase):
    __tablename__ = "exif_metadata_loras"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("exif_metadata.hash"),
        primary_key=True,
        init=False,
    )
    position: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=False,
    )
    name: Mapped[str]
    weight: Mapped[float]

    document: Mapped[ExifMetadata] = relationship(
        back_populates="loras",
        init=False,
        repr=False,
    )


class ExifMetadataPromptTag(ExifMetadataBase):
    __tablename__ = "exif_metadata_prompt_tags"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("exif_metadata.hash"),
        primary_key=True,
        init=False,
    )
    polarity: Mapped[str] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=False,
    )
    kind: Mapped[str]
    text: Mapped[str | None] = mapped_column(default=None)
    amplifier: Mapped[float | None] = mapped_column(default=None)
    parens: Mapped[int | None] = mapped_column(default=None)
    id2: Mapped[str | None] = mapped_column(default=None)
    id3: Mapped[str | None] = mapped_column(default=None)
    weight_float: Mapped[float | None] = mapped_column(default=None)
    weight_text: Mapped[str | None] = mapped_column(default=None)

    document: Mapped[ExifMetadata] = relationship(
        back_populates="prompt_tags",
        init=False,
        repr=False,
    )


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
        parent_ifd = (
            exif_by_name.get("ExifIFD", {}) if ifd_name == "InteropIFD" else exif
        )
        if ifd_id not in parent_ifd:
            continue

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

    def parse(
        self,
        prompt: str,
        *,
        polarity: str,
    ) -> list[ExifMetadataPromptTag]:
        return self.parse_tokens(
            self.tokenize(prompt),
            polarity=polarity,
        )

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

    def parse_tokens(
        self,
        tokens: list[str],
        *,
        polarity: str,
    ) -> list[ExifMetadataPromptTag]:
        result: list[ExifMetadataPromptTag] = []
        current_level = 0

        def add_tag(
            text: str,
            amplifier: float = 1.0,
        ) -> None:
            result.append(
                ExifMetadataPromptTag(
                    polarity=polarity,
                    position=len(result),
                    kind="tag",
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
                result.append(
                    ExifMetadataPromptTag(
                        polarity=polarity,
                        position=len(result),
                        kind="text",
                        text=self.break_token,
                    )
                )
            elif re.fullmatch(r"<[^>]+>", token):
                parts = token[1:-1].split(":")

                if len(parts) == 3:
                    id2 = parts[0]
                    id3 = parts[1]
                    weight = try_float(parts[2])
                elif len(parts) == 2:
                    id2 = parts[0]
                    id3 = None
                    weight = try_float(parts[1])
                else:
                    continue

                result.append(
                    ExifMetadataPromptTag(
                        polarity=polarity,
                        position=len(result),
                        kind="lora",
                        id2=id2,
                        id3=id3,
                        weight_float=(weight if isinstance(weight, float) else None),
                        weight_text=(weight if isinstance(weight, str) else None),
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
    *,
    file_hash: str,
) -> ExifMetadata:
    json_path = path.with_suffix(".json")

    result = ExifMetadata(
        hash=file_hash,
        original_path=str(path),
        reference_dir=str(reference_dir),
        image_time=datetime.fromtimestamp(path.stat().st_mtime),
    )

    if fast and not json_path.exists():
        return result

    sidecar_payload: dict[str, Any] = {}

    with Image.open(path) as img:
        result.image_path = str(path)
        result.size = img.size

        if json_path.exists():
            sidecar_payload = json.loads(json_path.read_text())
            metadata = sidecar_payload.get("exif_metadata", {})
            json_metadata = True
        else:
            metadata = dict(img.info)
            json_metadata = False

        embedded = _collect_all_embedded_metadata(img)
        result.original_metadata_full = _metadata_json_value(
            {
                "embedded": embedded,
                "sidecar": sidecar_payload,
                "exif_metadata": (
                    metadata if json_metadata else embedded["info_parsed"]
                ),
            }
        )

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
            result.clip_skip = int(parameters.ClipSkip.strip(","))
            result.cfg_scale = float(parameters.CFGScale.strip(","))

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
                        ExifMetadataLora(
                            position=len(result.loras),
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
        result.clip_skip = generation.clipSkip
        result.cfg_scale = generation.cfgScale
        result.size = (generation.width, generation.height)
        result.sd_vae = generation.sdVae
        result.eta_noise_seed_delta = generation.etaNoiseSeedDelta

        result.loras.extend(
            ExifMetadataLora(
                position=position,
                name=model.modelFileName,
                weight=model.weight,
            )
            for position, model in enumerate(generation.models)
        )

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
        result.prompt_tags.extend(parser.parse(result.prompt, polarity="positive"))
        result.parsed_prompt_present = True

    if result.negative_prompt:
        result.prompt_tags.extend(
            parser.parse(result.negative_prompt, polarity="negative")
        )
        result.parsed_negative_prompt_present = True

    return result


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

        try:
            document = get_image_params(
                path,
                path.parent,
                file_hash=file_hash,
            )
        except UnidentifiedImageError as exception:
            document = ExifMetadata(
                hash=file_hash,
                error=str(exception),
            )
        except OSError as exception:
            if "image file is truncated" not in str(exception):
                raise

            document = ExifMetadata(
                hash=file_hash,
                error=str(exception),
            )

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            existing = session.scalar(
                select(ExifMetadata.hash).where(ExifMetadata.hash == file_hash)
            )

            if existing is not None:
                return

            session.add(document)
            session.flush()
