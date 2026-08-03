from pathlib import Path

from beartype import beartype
from beartype.typing import Any, Callable

from haxdex.gui.file_tree.model.tree_model_fetch import AQL_FILE_PATHS, FilePathRow

from .constants import IMAGE_EXTS
from .models import (
    GenerationParamsIndexerResult,
    GenerationRowState,
    IndexedFileRecord,
    LoraState,
    SafetensorIndexerResult,
    WdTagIndexerResult,
)
from ...services.core.types import FileHash


@beartype
class ExplorerRepository:

    def __init__(
        self,
        index_database: Any,
        generation_params_indexer: Any,
        safetensor_indexer: Any,
        wd_tag_indexer: Any,
    ) -> None:
        self.index_database = index_database
        self.generation_params_indexer = generation_params_indexer
        self.safetensor_indexer = safetensor_indexer
        self.wd_tag_indexer = wd_tag_indexer

    def load_file_paths(self) -> list[FilePathRow]:
        cursor = self.index_database.db.aql.execute(AQL_FILE_PATHS)
        return [
            FilePathRow(
                path=item["path"],
                hash=item["hash"],
                root=item["root"],
                relative=item["relative"],
            ) for item in cursor
        ]

    def parse_generation_params(
            self, value: Any | None) -> GenerationParamsIndexerResult | None:
        if value is None:
            return None
        return GenerationParamsIndexerResult.model_validate(value)

    def parse_safetensor(self, value: Any | None) -> SafetensorIndexerResult | None:
        if value is None:
            return None
        return SafetensorIndexerResult.model_validate(value)

    def parse_wd_tags(self, value: Any | None) -> WdTagIndexerResult | None:
        if value is None:
            return None
        return WdTagIndexerResult.model_validate(value)

    def load_indexed_files(self) -> list[IndexedFileRecord]:
        file_paths = self.load_file_paths()
        hashes = [FileHash(hash=item.hash) for item in file_paths]

        generation_values = self.index_database.get_indexer_result_batch(
            hashes=hashes,
            indexer=self.generation_params_indexer,
        )

        safetensor_values = self.index_database.get_indexer_result_batch(
            hashes=hashes,
            indexer=self.safetensor_indexer,
        )

        wd_tag_values = self.index_database.get_indexer_result_batch(
            hashes=hashes,
            indexer=self.wd_tag_indexer,
        )

        records: list[IndexedFileRecord] = []
        for item, generation_value, safetensor_value, wd_tag_value in zip(
                file_paths, generation_values, safetensor_values, wd_tag_values,
                strict=True):
            records.append(
                IndexedFileRecord(
                    path=item.path,
                    file_hash=item.hash,
                    root=item.root,
                    relative=item.relative,
                    generation_params=self.parse_generation_params(generation_value),
                    safetensor=self.parse_safetensor(safetensor_value),
                    wd_tags=self.parse_wd_tags(wd_tag_value),
                ))

        return records

    def build_generation_rows(
            self, records: list[IndexedFileRecord]) -> list[GenerationRowState]:
        output: list[GenerationRowState] = []
        indexer_id = str(self.generation_params_indexer.asset_name)
        for record in records:
            extension = Path(record.path).suffix.lower()
            generation = record.generation_params
            match extension:
                case ".png" | ".jpg" | ".jpeg" | ".webp" | ".bmp":
                    pass
                case _:
                    continue
            if generation is None:
                continue
            if generation.positive.strip() == "":
                continue
            if generation.negative.strip() == "":
                continue
            lora_values = [
                LoraState(enabled=True, model=lora.model, weight=float(lora.weight))
                for lora in generation.loras
            ]
            output.append(
                GenerationRowState(
                    path=record.path,
                    md5=record.file_hash,
                    indexer_id=indexer_id,
                    positive=generation.positive,
                    negative=generation.negative,
                    width=int(generation.width),
                    height=int(generation.height),
                    steps=int(generation.steps),
                    cfg=float(generation.cfg),
                    checkpoint=generation.checkpoint,
                    loras=lora_values,
                    vae=generation.vae,
                    clip=generation.clip,
                ))
        return output

    def build_arch_info(self, records: list[IndexedFileRecord]) -> dict[str, str | None]:
        output: dict[str, str | None] = {}
        for record in records:
            safetensor = record.safetensor
            if safetensor is None:
                continue
            if record.relative == "":
                continue
            relative = Path(record.relative).with_suffix("").as_posix()
            architecture = safetensor.metadata.get("modelspec.architecture")
            match architecture:
                case str():
                    output[relative] = architecture
                case _:
                    output[relative] = None

        return output
