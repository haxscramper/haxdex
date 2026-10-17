from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from sqlalchemy import ForeignKey, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from haxdex_read_media.indexers.comfy_input_indexer import (
    ComfyInput,
    ComfyInputIndexer,
    ComfyInputNode,
)
from haxdex_read_media.indexers.exif_metadata import (
    ExifMetadata,
    ExifMetadataIndexer,
)

GENERATION_PARAMS_SCHEMA = MetaData()


class GENERATION_PARAMS_BASE(DeclarativeBase):
    metadata = GENERATION_PARAMS_SCHEMA


class GenerationParams(GENERATION_PARAMS_BASE):
    __tablename__ = "generation_params"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    positive: Mapped[str]
    negative: Mapped[str]
    width: Mapped[int]
    height: Mapped[int]
    sampler: Mapped[str]
    scheduler: Mapped[str]
    steps: Mapped[int]
    cfg: Mapped[float]
    checkpoint: Mapped[str]
    vae: Mapped[str | None]
    clip: Mapped[str | None]

    loras: Mapped[list[GenerationParamsLora]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="GenerationParamsLora.position",
    )


class GenerationParamsLora(GENERATION_PARAMS_BASE):
    __tablename__ = "generation_params_loras"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_params.hash"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=False,
    )
    model: Mapped[str]
    weight: Mapped[float]

    document: Mapped[GenerationParams] = relationship(
        back_populates="loras",
    )


def _normalize_sampler_name(name: str) -> tuple[str, str | None]:
    sampler_map = {
        "euler": "euler",
        "eulera": "euler_ancestral",
        "euleracfg++": "euler_ancestral_cfg_pp",
        "eulercfg++": "euler_cfg_pp",
        "heun": "heun",
        "heunpp2": "heunpp2",
        "dpm2": "dpm_2",
        "dpm2a": "dpm_2_ancestral",
        "lms": "lms",
        "dpmfast": "dpm_fast",
        "dpmadaptive": "dpm_adaptive",
        "dpm++2sa": "dpmpp_2s_ancestral",
        "dpm++sde": "dpmpp_sde",
        "dpm++2m": "dpmpp_2m",
        "dpm++2msde": "dpmpp_2m_sde",
        "dpm++3msde": "dpmpp_3m_sde",
        "ddpm": "ddpm",
        "lcm": "lcm",
        "ddim": "ddim",
        "unipc": "uni_pc",
        "unipcbh2": "uni_pc_bh2",
        "ipndm": "ipndm",
        "deis": "deis",
    }

    scheduler_map = {
        "normal": "normal",
        "karras": "karras",
        "exponential": "exponential",
        "sgmuniform": "sgm_uniform",
        "simple": "simple",
        "ddimuniform": "ddim_uniform",
        "beta": "beta",
        "linearquadratic": "linear_quadratic",
        "kloptimal": "kl_optimal",
    }

    key = name.lower().replace(" ", "")

    scheduler: str | None = None
    for suffix, value in scheduler_map.items():
        if key.endswith(suffix) and key != suffix:
            scheduler = value
            key = key[: -len(suffix)]
            break

    if key in sampler_map:
        return sampler_map[key], scheduler

    return key, "normal"


def _extract_generation_params(
    document: ComfyInput,
    file_hash: str,
) -> GenerationParams:
    by_id = {node.node_id: node for node in document.nodes}

    dependencies: dict[str, set[str]] = defaultdict(set)
    for link in document.links:
        dependencies[link.target_id].add(link.origin_id)

    dependents: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {}

    for node in document.nodes:
        deps = dependencies[node.node_id]
        in_degree[node.node_id] = len(deps)
        for dep in deps:
            dependents[dep].append(node.node_id)

    queue = deque(node_id for node_id, degree in in_degree.items() if degree == 0)
    order: list[str] = []

    while queue:
        node_id = queue.popleft()
        order.append(node_id)

        for dependent in dependents[node_id]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    result = GenerationParams(
        hash=file_hash,
        positive="",
        negative="",
        width=0,
        height=0,
        sampler="",
        scheduler="",
        steps=0,
        cfg=0.0,
        checkpoint="",
        loras=[],
        vae=None,
        clip=None,
    )

    def get(node: ComfyInputNode, key: str, index: int) -> Any:
        if isinstance(node.inputs, dict):
            return node.inputs.get(key)
        if 0 <= index < len(node.inputs):
            return node.inputs[index]
        return None

    def add_lora(model: str, weight: float) -> None:
        result.loras.append(
            GenerationParamsLora(
                position=len(result.loras),
                model=model,
                weight=weight,
            )
        )

    for node_id in order:
        node = by_id[node_id]

        match (node.node, node.title):
            case ("CLIPTextEncode", "CLIP Text Encode (Positive Prompt)"):
                result.positive = get(node, "text", 0) or result.positive

            case ("CLIPTextEncode", "CLIP Text Encode (Negative Prompt)"):
                result.negative = get(node, "text", 0) or result.negative

            case ("CLIPTextEncode", _):
                if not result.positive:
                    result.positive = get(node, "text", 0) or result.positive
                elif not result.negative:
                    result.negative = get(node, "text", 0) or result.negative

            case ("EmptyLatentImage", _):
                result.width = get(node, "width", 0) or result.width
                result.height = get(node, "height", 1) or result.height

            case ("KSampler", _):
                result.steps = get(node, "steps", 2) or result.steps
                result.cfg = get(node, "cfg", 3) or result.cfg
                result.sampler = get(node, "sampler_name", 4) or result.sampler
                result.scheduler = get(node, "scheduler", 5) or result.scheduler

            case ("CheckpointLoaderSimple", _) | ("CheckpointLoader", _):
                result.checkpoint = get(node, "ckpt_name", 0) or result.checkpoint

            case ("LoraLoader", _) | ("LoraLoaderModelOnly", _):
                add_lora(
                    model=get(node, "lora_name", 0),
                    weight=get(node, "strength_model", 1),
                )

            case ("Power Lora Loader (rgthree)", _):
                inputs = (
                    node.inputs.values()
                    if isinstance(node.inputs, dict)
                    else node.inputs
                )
                for value in inputs:
                    if (
                        isinstance(value, dict)
                        and "lora" in value
                        and value.get("on", True)
                    ):
                        add_lora(
                            model=value["lora"],
                            weight=value["strength"],
                        )

            case ("VAELoader", _):
                result.vae = get(node, "vae_name", 0) or result.vae

            case ("CLIPLoader", _):
                result.clip = get(node, "clip_name", 0) or result.clip

    return result


def _extract_exif_generation_params(
    document: ExifMetadata,
    file_hash: str,
) -> GenerationParams:
    sampler, scheduler = _normalize_sampler_name(document.sampler)

    return GenerationParams(
        hash=file_hash,
        positive=document.prompt,
        negative=document.negative_prompt,
        width=document.width,
        height=document.height,
        checkpoint=document.model,
        steps=document.steps,
        sampler=sampler,
        cfg=document.cfg_scale,
        scheduler=scheduler or "normal",
        loras=[
            GenerationParamsLora(
                position=position,
                model=lora.name,
                weight=lora.weight,
            )
            for position, lora in enumerate(document.loras)
        ],
        vae=None,
        clip=None,
    )


class GenerationParamsIndexer(BaseIndexer):
    asset_name = "generation_params"
    result_schema = GENERATION_PARAMS_SCHEMA
    table = GenerationParams.__table__
    required_assets = (
        ExifMetadataIndexer.asset_name,
        ComfyInputIndexer.asset_name,
    )

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            if session.get(GenerationParams, file_hash) is not None:
                return

            with (
                assets[ComfyInputIndexer.asset_name].connection() as comfy_connection,
                Session(bind=comfy_connection) as comfy_session,
            ):
                comfy = comfy_session.get(ComfyInput, file_hash)
                if comfy is None:
                    raise LookupError(f"Missing ComfyInput for {file_hash}")

                if comfy.note == "no workflow data detected":
                    with (
                        assets[
                            ExifMetadataIndexer.asset_name
                        ].connection() as exif_connection,
                        Session(bind=exif_connection) as exif_session,
                    ):
                        exif = exif_session.get(ExifMetadata, file_hash)
                        if exif is None:
                            raise LookupError(f"Missing ExifMetadata for {file_hash}")

                        result = _extract_exif_generation_params(exif, file_hash)
                else:
                    result = _extract_generation_params(comfy, file_hash)

            session.add(result)
            session.flush()
